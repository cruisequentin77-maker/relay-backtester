"""Unit tests for the ChiNext martingale engine."""
from martingale import Bar, MartingaleConfig, run_martingale


def _run(closes, **kwargs):
    params = dict(initial_cash=7_000.0, fee_rate=0.0, max_layers=3, drop_step=0.05, take_profit=0.05)
    params.update(kwargs)
    bars = [Bar(date=f"2020-01-{index + 1:02d}", close=price) for index, price in enumerate(closes)]
    return run_martingale(bars, MartingaleConfig(**params))


def test_take_profit_on_a_rising_path():
    result = _run([100, 100, 106])
    closed = [trade for trade in result["trades"] if not trade.get("forced")]
    assert len(closed) == 1
    assert closed[0]["layers"] == 1
    assert closed[0]["pnl"] == 60
    assert result["stats"]["strategy_final"] == 7_060


def test_doubles_after_a_five_percent_drop_then_exits_above_average():
    result = _run([100, 94, 110])
    closed = [trade for trade in result["trades"] if not trade.get("forced")]
    assert len(closed) == 1
    assert closed[0]["layers"] == 2
    assert closed[0]["pnl"] > 0
    assert result["stats"]["max_layer_reached"] == 2


def test_does_not_add_past_the_layer_cap():
    result = _run([100, 90, 80, 70], max_layers=2, take_profit=0.50)
    assert result["stats"]["max_layer_reached"] == 2
    assert all(point["layer"] <= 2 for point in result["curve"])


def test_cash_stays_non_negative_on_a_decline():
    closes = [100 * (0.97 ** index) for index in range(12)]
    result = _run(closes, max_layers=4, take_profit=0.20, initial_cash=100_000)
    assert result["stats"]["strategy_final"] > 0
    assert result["stats"]["min_cash"] >= -0.01
    assert min(point["equity"] for point in result["curve"]) > 0


if __name__ == "__main__":
    test_take_profit_on_a_rising_path()
    test_doubles_after_a_five_percent_drop_then_exits_above_average()
    test_does_not_add_past_the_layer_cap()
    test_cash_stays_non_negative_on_a_decline()
    print("ALL MARTINGALE TESTS PASSED")
