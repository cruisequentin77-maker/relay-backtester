"""
Unit tests for the 5-Agent Relay Backtesting Engine (Crypto & Stock modes).
"""
from engine import RelayRuleConfig, run_relay_backtest


def test_crypto_default_video_parity():
    cfg = RelayRuleConfig(mode="crypto")
    res = run_relay_backtest(cfg)
    assert res["mode"] == "crypto"
    assert res["initial_balance"] == 100.0
    assert res["final_balance"] == 4812.0
    assert res["funnel"]["total_pairs"] == 14200
    assert res["funnel"]["survived_finder"] == 312
    assert res["funnel"]["survived_auditor"] == 118
    assert res["funnel"]["trades_taken"] == 64
    assert res["funnel"]["said_no_pct"] == 99.5
    assert len(res["daily_timeline"]) == 9
    print("Crypto mode verified:", res["final_balance"], res["funnel"])


def test_stock_mode():
    cfg = RelayRuleConfig(
        mode="stock",
        desk_id="Desk 0002",
        initial_balance=10000.0,
        total_candidates=4800,
        rule1_min_liquidity_or_vol=2.0,
        rule2_max_age_or_gap=3.0,
        rule6_position_size_pct=10.0,
        rule6_stop_loss_pct=6.0,
        daily_stop_pct=4.0,
        tp1_gain_pct=20.0,
        tp2_gain_pct=40.0,
        trail_pct=8.0,
    )
    res = run_relay_backtest(cfg)
    assert res["mode"] == "stock"
    assert res["initial_balance"] == 10000.0
    assert res["final_balance"] > 10000.0
    assert len(res["daily_timeline"]) == 9
    print("Stock mode verified:", res["final_balance"], res["funnel"])


if __name__ == "__main__":
    test_crypto_default_video_parity()
    test_stock_mode()
    print("ALL ENGINE TESTS PASSED!")
