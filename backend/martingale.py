"""
Martingale backtest on the ChiNext index (创业板指 399006).

Long-only, daily close fills:
- Flat -> buy one base unit.
- Close falls `drop_step` below the last fill -> buy double the previous unit.
- Close rises `take_profit` above average cost -> sell the whole cycle.
- At most one add per day, and at most `max_layers` fills.
- Base unit is resized at each new cycle so a full pyramid (1+2+...+2^(n-1))
  plus fees fits inside current equity. Leftover cash stays uninvested.
"""
from __future__ import annotations

import csv
import json
import math
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DATA_PATH = Path(__file__).resolve().parent / "data" / "399006.csv"
EASTMONEY_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid=0.399006"
    "&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    "&klt=101&fqt=0&beg=20100601&end=20500101&lmt=10000"
)


@dataclass(frozen=True)
class Bar:
    date: str
    close: float


@dataclass
class MartingaleConfig:
    initial_cash: float = 1_000_000.0
    max_layers: int = 6
    drop_step: float = 0.05
    take_profit: float = 0.05
    fee_rate: float = 0.0003

    def validate(self) -> None:
        if self.initial_cash < 1_000:
            raise ValueError("initial_cash must be >= 1000")
        if not 1 <= self.max_layers <= 8:
            raise ValueError("max_layers must be between 1 and 8")
        if not 0.005 <= self.drop_step <= 0.30:
            raise ValueError("drop_step must be between 0.5% and 30%")
        if not 0.005 <= self.take_profit <= 0.50:
            raise ValueError("take_profit must be between 0.5% and 50%")
        if not 0 <= self.fee_rate <= 0.01:
            raise ValueError("fee_rate must be between 0 and 1%")


def refresh_chinext_bars(path: Path = DATA_PATH) -> int:
    """Download unadjusted daily bars for 创业板指 from East Money."""
    request = urllib.request.Request(
        EASTMONEY_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    klines = payload.get("data", {}).get("klines") or []
    if not klines:
        raise RuntimeError("East Money returned no klines for 399006")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "amount"])
        for row in klines:
            parts = row.split(",")
            # date, open, close, high, low, volume, amount, ...
            writer.writerow([
                parts[0],
                parts[1],
                parts[3],
                parts[4],
                parts[2],
                parts[5],
                parts[6],
            ])
    return len(klines)


def load_chinext_bars(path: Path = DATA_PATH) -> List[Bar]:
    if not path.exists():
        refresh_chinext_bars(path)
    bars: List[Bar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            close = float(row["close"])
            if close <= 0:
                continue
            bars.append(Bar(date=row["date"], close=close))
    if len(bars) < 2:
        raise RuntimeError(f"not enough bars in {path}")
    return bars


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _sharpe(equity: Sequence[float]) -> float:
    if len(equity) < 3:
        return 0.0
    returns = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    if var <= 0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(252.0)


def _cagr(initial: float, final: float, start: str, end: str) -> float:
    days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
    if days <= 0 or initial <= 0 or final <= 0:
        return 0.0
    return (final / initial) ** (365.25 / days) - 1.0


def _yearly(dates: Sequence[str], equity: Sequence[float], initial: float) -> List[Dict[str, Any]]:
    buckets: List[Dict[str, Any]] = []
    year_start_equity = initial
    current_year = dates[0][:4]
    last_equity = equity[0]
    for date, value in zip(dates, equity):
        year = date[:4]
        if year != current_year:
            buckets.append({
                "year": current_year,
                "return": last_equity / year_start_equity - 1.0 if year_start_equity else 0.0,
            })
            year_start_equity = last_equity
            current_year = year
        last_equity = value
    buckets.append({
        "year": current_year,
        "return": last_equity / year_start_equity - 1.0 if year_start_equity else 0.0,
    })
    return buckets


def _buy_and_hold(bars: Sequence[Bar], initial_cash: float, fee_rate: float) -> List[float]:
    first = bars[0].close
    gross = initial_cash / (1.0 + fee_rate)
    shares = gross / first
    curve = []
    for bar in bars:
        market = shares * bar.close
        # Charge the exit fee only on the last mark, so the curve stays comparable
        # to a strategy that is still holding.
        curve.append(market)
    curve[-1] = shares * bars[-1].close * (1.0 - fee_rate)
    return curve


def run_martingale(bars: Sequence[Bar], cfg: MartingaleConfig) -> Dict[str, Any]:
    cfg.validate()
    if len(bars) < 2:
        raise ValueError("need at least 2 bars")

    cash = cfg.initial_cash
    shares = 0.0
    cost_basis = 0.0
    last_entry = 0.0
    layer = 0
    cycle_base = 0.0
    entry_date = ""
    entry_index = 0
    fees_paid = 0.0
    max_layer_reached = 0
    pyramid_sum = (2 ** cfg.max_layers) - 1

    equity_curve: List[float] = []
    layers_curve: List[int] = []
    exposure_sum = 0.0
    min_cash = cash
    trades: List[Dict[str, Any]] = []

    def buy(price: float, notional: float, date: str, index: int) -> bool:
        nonlocal cash, shares, cost_basis, last_entry, layer, fees_paid, max_layer_reached, entry_date, entry_index
        affordable = cash / (1.0 + cfg.fee_rate)
        if affordable <= 1.0:
            return False
        fill_notional = min(notional, affordable)
        # Do not partial-fill a double. A new cycle may use the full affordable base.
        if layer > 0 and fill_notional < notional * 0.999:
            return False
        fee = fill_notional * cfg.fee_rate
        got = fill_notional / price
        cash -= fill_notional + fee
        shares += got
        cost_basis += fill_notional + fee
        fees_paid += fee
        last_entry = price
        layer += 1
        max_layer_reached = max(max_layer_reached, layer)
        if layer == 1:
            entry_date = date
            entry_index = index
        return True

    def sell(price: float, date: str, index: int) -> None:
        nonlocal cash, shares, cost_basis, layer, last_entry, fees_paid
        proceeds_gross = shares * price
        fee = proceeds_gross * cfg.fee_rate
        proceeds = proceeds_gross - fee
        pnl = proceeds - cost_basis
        trades.append({
            "entry_date": entry_date,
            "exit_date": date,
            "hold_days": index - entry_index,
            "layers": layer,
            "avg_cost": cost_basis / shares if shares else 0.0,
            "exit_price": price,
            "pnl": pnl,
            "return": pnl / cost_basis if cost_basis else 0.0,
        })
        cash += proceeds
        fees_paid += fee
        shares = 0.0
        cost_basis = 0.0
        layer = 0
        last_entry = 0.0

    last_index = len(bars) - 1
    for index, bar in enumerate(bars):
        price = bar.close
        if layer == 0:
            # Don't open a brand-new cycle on the final bar; it would be closed
            # at the same price a few lines later.
            if index < last_index:
                cycle_base = cash / (pyramid_sum * (1.0 + cfg.fee_rate))
                buy(price, cycle_base, bar.date, index)
        else:
            average = cost_basis / shares
            if price >= average * (1.0 + cfg.take_profit):
                sell(price, bar.date, index)
            elif layer < cfg.max_layers and price <= last_entry * (1.0 - cfg.drop_step):
                buy(price, cycle_base * (2 ** layer), bar.date, index)

        market = shares * price
        equity = cash + market
        min_cash = min(min_cash, cash)
        equity_curve.append(equity)
        layers_curve.append(layer)
        exposure_sum += (market / equity) if equity > 0 else 0.0

    # Liquidate a leftover cycle so the headline equity is realizable.
    open_cycle: Optional[Dict[str, Any]] = None
    if shares > 0:
        price = bars[-1].close
        average = cost_basis / shares
        unrealized = shares * price * (1.0 - cfg.fee_rate) - cost_basis
        open_cycle = {
            "entry_date": entry_date,
            "layers": layer,
            "avg_cost": average,
            "last_price": price,
            "unrealized_pnl": unrealized,
            "unrealized_return": unrealized / cost_basis if cost_basis else 0.0,
        }
        sell(price, bars[-1].date, len(bars) - 1)
        # The forced exit is not a strategy take-profit. Drop it from the win sample
        # but keep the cash it realizes. Tag the last trade.
        trades[-1]["forced"] = True
        equity_curve[-1] = cash

    bh_curve = _buy_and_hold(bars, cfg.initial_cash, cfg.fee_rate)
    dates = [bar.date for bar in bars]
    final_equity = equity_curve[-1]
    bh_final = bh_curve[-1]
    closed = [trade for trade in trades if not trade.get("forced")]
    wins = [trade for trade in closed if trade["pnl"] > 0]
    yearly_strategy = _yearly(dates, equity_curve, cfg.initial_cash)
    yearly_bh = _yearly(dates, bh_curve, cfg.initial_cash)
    yearly = []
    for left, right in zip(yearly_strategy, yearly_bh):
        yearly.append({
            "year": left["year"],
            "strategy": left["return"],
            "buy_hold": right["return"],
            "excess": left["return"] - right["return"],
        })

    points = [
        {
            "date": bar.date,
            "close": bar.close,
            "equity": equity_curve[i],
            "buy_hold": bh_curve[i],
            "layer": layers_curve[i],
        }
        for i, bar in enumerate(bars)
    ]

    def pack_return(final: float) -> Dict[str, float]:
        return {
            "final": final,
            "total_return": final / cfg.initial_cash - 1.0,
            "cagr": _cagr(cfg.initial_cash, final, dates[0], dates[-1]),
            "max_drawdown": _max_drawdown(
                equity_curve if final == final_equity else bh_curve
            ),
            "sharpe": _sharpe(equity_curve if final == final_equity else bh_curve),
        }

    strategy_stats = pack_return(final_equity)
    bh_stats = {
        "final": bh_final,
        "total_return": bh_final / cfg.initial_cash - 1.0,
        "cagr": _cagr(cfg.initial_cash, bh_final, dates[0], dates[-1]),
        "max_drawdown": _max_drawdown(bh_curve),
        "sharpe": _sharpe(bh_curve),
    }

    return {
        "symbol": "399006",
        "name": "创业板指",
        "source": "仓库打包的创业板指公开日线（东方财富 secid=0.399006，不复权）。不是合成数据，也不是实时行情。",
        "start": dates[0],
        "end": dates[-1],
        "bars": len(bars),
        "params": {
            "initial_cash": cfg.initial_cash,
            "max_layers": cfg.max_layers,
            "drop_step": cfg.drop_step,
            "take_profit": cfg.take_profit,
            "fee_rate": cfg.fee_rate,
            "sizing": "each new cycle sizes the base unit so 1+2+...+2^(n-1) plus fees equals equity",
        },
        "rules": [
            "空仓时按收盘价买入 1 份基础仓。基础仓在每一轮开仓时按当前权益重算，完整金字塔刚好用完权益。",
            f"收盘价较最近一次买入价下跌 {cfg.drop_step:.1%} 及以上，下一档按上一档金额的 2 倍买入。每个交易日最多加一档。",
            f"收盘价高于持仓均价 {cfg.take_profit:.1%}（均价含手续费）时，全部卖出，层数归零，下一交易日重新开基础仓。",
            f"最多 {cfg.max_layers} 档（金额 1, 2, 4 ...）。加满后只持有，直到止盈。",
            f"单边手续费 {cfg.fee_rate:.2%}，按收盘价成交。创业板指本身不可交易，这是指数点位上的模拟，成本按 ETF 单边万三近似。",
        ],
        "stats": {
            **{f"strategy_{key}": value for key, value in strategy_stats.items()},
            **{f"buy_hold_{key}": value for key, value in bh_stats.items()},
            "excess_total_return": strategy_stats["total_return"] - bh_stats["total_return"],
            "closed_trades": len(closed),
            "win_rate": (len(wins) / len(closed)) if closed else 0.0,
            "avg_hold_days": (sum(trade["hold_days"] for trade in closed) / len(closed)) if closed else 0.0,
            "avg_trade_return": (sum(trade["return"] for trade in closed) / len(closed)) if closed else 0.0,
            "max_layer_reached": max_layer_reached,
            "avg_exposure": exposure_sum / len(bars),
            "fees_paid": fees_paid,
            "min_cash": min_cash,
        },
        "open_cycle": open_cycle,
        "yearly": yearly,
        "trades": trades,
        "curve": points,
    }
