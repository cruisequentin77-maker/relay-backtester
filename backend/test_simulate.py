"""Index / symbol bar-simulation tests, including the event-stream contract."""
from datetime import date, timedelta
from pathlib import Path
import tempfile

from fastapi import HTTPException
from pydantic import ValidationError

from main import BacktestRequest, execute_backtest, get_catalog, get_presets
from market_data import Bar, load_bars, synthesize_bars, resolve_spec
from simulate import EVENT_ACTIONS, EVENT_KEYS, apply_prompt, default_config, rules_for, run_symbol_backtest


def _dates(count, start=date(2020, 1, 2)):
    cursor = start
    days = []
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _bars(closes, volumes=None):
    days = _dates(len(closes))
    bars = []
    for index, close in enumerate(closes):
        prev = closes[index - 1] if index else close
        opened = prev
        high = max(opened, close) * 1.001
        low = min(opened, close) * 0.999
        volume = 1_000_000 if volumes is None else volumes[index]
        bars.append(Bar(days[index], opened, high, low, close, volume))
    return bars


def _assert_event_contract(result):
    events = result["events"]
    assert events, "event stream is empty"
    assert [event["date"] for event in events] == sorted(event["date"] for event in events)
    assert events[0]["date"] < events[-1]["date"]
    for event in events:
        for key in EVENT_KEYS:
            assert key in event
        assert event["action"] in EVENT_ACTIONS
        assert event["agent"] in {"", "Finder", "Auditor", "Judge", "Stager", "Closer"}
        assert isinstance(event["fired_rules"], list)
        equity = event["cash"] + event["position_qty"] * event["close"]
        assert abs(event["equity"] - equity) < 0.05
        assert event["cash"] >= -0.05
        assert event["position_qty"] >= -1e-8
    summary = result["summary"]
    for key in ("total_return_pct", "max_drawdown_pct", "trade_count", "win_rate", "final_equity"):
        assert key in summary
    assert summary["max_drawdown_pct"] <= 0.05
    assert summary["trade_count"] == len(result["trades"])
    assert result["engine"] == "bar"


def test_index_rules_are_not_copied_from_meme_or_fundamental_filters():
    for style in ("trend", "breakout", "mean_reversion"):
        cfg = default_config("index")
        cfg.style = style
        blob = " ".join(rule["text"] for rule in rules_for(cfg)).lower()
        for banned in ("holder", "mint", "freeze", "市盈", "负债", "前十", "合约权限", "debt"):
            assert banned not in blob
        assert "均线" in blob
        agents = {rule["agent"] for rule in rules_for(cfg)}
        assert agents == {"Finder", "Auditor", "Judge", "Stager", "Closer"}


def test_rising_index_enters_and_a_crash_hits_the_hard_stop():
    price = 100.0
    closes = []
    for _ in range(170):
        price *= 1.003
        closes.append(price)
    bars = _bars(closes)
    last = bars[-1].close
    crash_day = _dates(len(bars) + 1)[-1]
    bars.append(Bar(crash_day, last, last, last * 0.5, last * 0.7, 2_000_000))
    cfg = default_config("index")
    cfg.lookback = 120
    cfg.symbol = "TEST"
    result = run_symbol_backtest(bars, cfg)
    _assert_event_contract(result)
    assert result["mode"] == "index"
    assert result["trade_count"] >= 1
    assert any(event["action"] == "entry" for event in result["events"])
    assert any(event["action"] == "exit" and event["rule_id"] == "R6" for event in result["events"])


def test_stock_and_crypto_rules_use_their_own_evidence():
    stock = " ".join(rule["text"] for rule in rules_for(default_config("stock")))
    crypto = " ".join(rule["text"] for rule in rules_for(default_config("crypto")))
    assert "成交量" in stock
    assert "跳空" in stock
    assert "成交额" in crypto
    assert "链上" in crypto
    price = 50.0
    closes = []
    volumes = []
    for index in range(140):
        price *= 1.004 if index % 25 < 18 else 0.997
        closes.append(price)
        volumes.append(2_000_000 if index % 7 == 0 else 1_000_000)
    cfg = default_config("stock")
    cfg.lookback = 80
    cfg.min_score = 50
    result = run_symbol_backtest(_bars(closes, volumes), cfg)
    _assert_event_contract(result)
    assert result["mode"] == "stock"

    crypto_cfg = default_config("crypto")
    crypto_cfg.lookback = 80
    crypto_result = run_symbol_backtest(_bars(closes, volumes), crypto_cfg)
    _assert_event_contract(crypto_result)
    assert crypto_result["mode"] == "crypto"


def test_prompt_overrides_index_parameters():
    cfg = default_config("index")
    apply_prompt(cfg, "改为突破。快线10 慢线40。仓位25%，止损6%，移动止盈9%，止盈8%/15%。评分70。近120根。波动上限5%。")
    assert cfg.style == "breakout"
    assert cfg.ma_fast == 10
    assert cfg.ma_slow == 40
    assert cfg.position_size_pct == 25
    assert cfg.stop_loss_pct == 6
    assert cfg.trail_pct == 9
    assert cfg.tp1_gain_pct == 8
    assert cfg.tp2_gain_pct == 15
    assert cfg.min_score == 70
    assert cfg.lookback == 120
    assert cfg.max_atr_pct == 5
    cfg.validate()


def test_bundled_index_symbols_and_alias():
    bars, meta = load_bars("000300", mode_hint="index")
    assert meta["source"] == "bundled"
    assert meta["provenance"] == "bundled_public_daily"
    assert meta["historical"] is True
    assert meta["name"] == "沪深300"
    assert "不是合成数据" in meta["note"]
    assert bars[0].date == "2018-01-02"
    alias_bars, alias_meta = load_bars("399300", mode_hint="index")
    assert alias_meta["symbol"] == "000300"
    assert len(alias_bars) == len(bars)
    for symbol in ("000905", "399006", "000016", "NDX", "SPX"):
        loaded, info = load_bars(symbol, mode_hint="index")
        assert info["source"] == "bundled"
        assert len(loaded) > 200
    result = run_symbol_backtest(bars, default_config("index"))
    _assert_event_contract(result)
    assert result["trade_count"] >= 1
    assert result["funnel"]["bars"] == len(result["events"])


def test_missing_bundle_falls_back_to_sample_without_raising():
    spec = resolve_spec("000300")
    with tempfile.TemporaryDirectory() as tmp:
        def boom(spec_arg, path):
            raise RuntimeError("network down")

        bars, meta = load_bars(
            "000300",
            directory=Path(tmp),
            allow_remote=True,
            fetcher=boom,
        )
        assert meta["source"] == "sample"
        assert meta["provenance"] == "synthetic_demonstration"
        assert meta["historical"] is False
        assert meta["name"].startswith("演示序列")
        assert "不是历史行情" in meta["note"]
        assert bars[0].date == "SYNTH-0001"
        assert bars[0].open < 200
        assert meta["warning"]
        assert spec.symbol == "000300"
        result = run_symbol_backtest(bars, default_config("index"))
        _assert_event_contract(result)
        assert result["events"][0]["date"].startswith("SYNTH-")


def test_catalog_and_relay_api_stay_compatible():
    presets = get_presets()
    assert presets["crypto"]["final_balance"] == 4812.0
    assert presets["crypto"]["engine"] == "relay"
    assert presets["stock"]["mode"] == "stock"
    assert "daily_timeline" in presets["stock"]
    index_symbols = {item["symbol"] for item in presets["catalog"]["index"]}
    assert {"000300", "000905", "399006", "000016", "NDX", "SPX"} <= index_symbols
    stock_symbols = {item["symbol"] for item in presets["catalog"]["stock"]}
    assert {"600519", "300750", "000858", "601318"} <= stock_symbols
    crypto_symbols = {item["symbol"] for item in presets["catalog"]["crypto"]}
    assert {"BTCUSDT", "ETHUSDT"} <= crypto_symbols
    catalog = get_catalog()
    assert catalog["bar_defaults"]["index"]["symbol"] == "000300"

    relay = execute_backtest(BacktestRequest(mode="crypto"))
    assert relay["engine"] == "relay"
    assert relay["final_balance"] == 4812.0
    assert "events" not in relay

    stock_relay = execute_backtest(BacktestRequest(mode="stock"))
    assert stock_relay["engine"] == "relay"
    assert stock_relay["initial_balance"] == 10000.0

    streamed = execute_backtest(BacktestRequest(mode="index", symbol="000016", lookback=160))
    _assert_event_contract(streamed)
    assert streamed["symbol"] == "000016"
    assert streamed["name"] == "上证50"
    assert streamed["data"]["provenance"] == "bundled_public_daily"
    assert streamed["data"]["historical"] is True

    btc = execute_backtest(BacktestRequest(mode="crypto", symbol="BTCUSDT", lookback=120))
    _assert_event_contract(btc)
    assert btc["currency"] == "USDT"

    moutai = execute_backtest(BacktestRequest(mode="stock", symbol="600519", lookback=120))
    _assert_event_contract(moutai)
    assert moutai["name"] == "贵州茅台"

    try:
        execute_backtest(BacktestRequest(mode="index", symbol="BTCUSDT"))
        raise AssertionError("mode mismatch should fail")
    except HTTPException as exc:
        assert exc.status_code == 400

    try:
        execute_backtest(BacktestRequest(mode="fx"))
        raise AssertionError("bad mode should fail")
    except HTTPException as exc:
        assert exc.status_code == 400


def test_request_limits_reject_oversized_input():
    try:
        BacktestRequest(mode="index", days=500, lookback=5000, prompt_text="x" * 4000)
        raise AssertionError("oversized request should fail validation")
    except ValidationError:
        pass
    ok = BacktestRequest(mode="index", symbol="000300", lookback=120)
    assert ok.lookback == 120


def test_sample_generator_is_deterministic():
    spec = resolve_spec("ETHUSDT")
    first = synthesize_bars(spec, 40)
    second = synthesize_bars(spec, 40)
    assert [(bar.date, bar.close) for bar in first] == [(bar.date, bar.close) for bar in second]


if __name__ == "__main__":
    test_index_rules_are_not_copied_from_meme_or_fundamental_filters()
    test_rising_index_enters_and_a_crash_hits_the_hard_stop()
    test_stock_and_crypto_rules_use_their_own_evidence()
    test_prompt_overrides_index_parameters()
    test_bundled_index_symbols_and_alias()
    test_missing_bundle_falls_back_to_sample_without_raising()
    test_catalog_and_relay_api_stay_compatible()
    test_request_limits_reject_oversized_input()
    test_sample_generator_is_deterministic()
    print("ALL SIMULATION TESTS PASSED")
