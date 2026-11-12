"""Bar-by-bar rule backtest for one crypto pair, broad index, or stock.

The five agents stay in relay order. What changes by asset class is the
evidence each rule is allowed to see:

- index: trend, breakout, moving-average alignment, mean reversion, volatility
- stock: relative volume, breakout or moving averages, gap failure, volatility
- crypto: quote-volume liquidity, breakout, extension, volatility shock

Fills are next-bar open after a close signal. Stops are assumed to fill before
targets when the same bar trades through both. Index rules do not read holder
concentration, contract permissions, PE, or debt.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from market_data import Bar

EVENT_ACTIONS = {
    "warmup",
    "hold",
    "skip",
    "kill",
    "reject",
    "signal",
    "entry",
    "scale_out",
    "exit",
    "halt",
}
EVENT_KEYS = (
    "i",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "equity",
    "cash",
    "position_qty",
    "position_value",
    "avg_price",
    "agent",
    "rule_id",
    "action",
    "reason",
    "fired_rules",
)


@dataclass
class SimConfig:
    mode: str = "index"
    symbol: str = "000300"
    style: str = "trend"
    initial_cash: float = 100_000.0
    lookback: int = 480
    ma_fast: int = 20
    ma_slow: int = 60
    breakout_lookback: int = 20
    min_breakout_pct: float = 0.0
    pullback_pct: float = 3.0
    max_atr_pct: float = 4.0
    max_extension_pct: float = 12.0
    min_rel_volume: float = 0.8
    min_score: int = 55
    position_size_pct: float = 40.0
    stop_loss_pct: float = 8.0
    tp1_gain_pct: float = 10.0
    tp2_gain_pct: float = 20.0
    trail_pct: float = 8.0
    daily_stop_pct: float = 4.0
    gap_pct: float = 6.0
    fee_rate: float = 0.0002

    def validate(self) -> None:
        if self.mode not in {"crypto", "index", "stock"}:
            raise ValueError("mode 只能是 crypto、index 或 stock")
        if self.style not in {"trend", "breakout", "mean_reversion"}:
            raise ValueError("style 只能是 trend、breakout 或 mean_reversion")
        if self.initial_cash < 1000:
            raise ValueError("初始资金至少为 1000")
        if not 40 <= self.lookback <= 3000:
            raise ValueError("回看窗口需要在 40 到 3000 根K线之间")
        if not 2 <= self.ma_fast < self.ma_slow <= 250:
            raise ValueError("快均线必须短于慢均线，且慢均线不超过 250")
        if not 5 <= self.breakout_lookback <= 120:
            raise ValueError("突破窗口需要在 5 到 120 之间")
        if not 0 <= self.min_breakout_pct <= 20:
            raise ValueError("突破幅度需要在 0% 到 20% 之间")
        if not 0.2 <= self.pullback_pct <= 20:
            raise ValueError("回撤阈值需要在 0.2% 到 20% 之间")
        if not 0.2 <= self.max_atr_pct <= 40:
            raise ValueError("波动上限需要在 0.2% 到 40% 之间")
        if not 0.5 <= self.max_extension_pct <= 80:
            raise ValueError("偏离上限需要在 0.5% 到 80% 之间")
        if not 0.1 <= self.min_rel_volume <= 10:
            raise ValueError("量能阈值需要在 0.1 到 10 倍之间")
        if not 1 <= int(self.min_score) <= 99:
            raise ValueError("评分阈值需要在 1 到 99 之间")
        if not 1 <= self.position_size_pct <= 100:
            raise ValueError("仓位需要在 1% 到 100% 之间")
        if not 0.5 <= self.stop_loss_pct <= 50:
            raise ValueError("止损需要在 0.5% 到 50% 之间")
        if not 0.5 <= self.tp1_gain_pct < self.tp2_gain_pct <= 500:
            raise ValueError("止盈需要满足 0.5% ≤ 第一目标 < 第二目标 ≤ 500%")
        if not 0.5 <= self.trail_pct <= 50:
            raise ValueError("移动止盈回撤需要在 0.5% 到 50% 之间")
        if not 0.5 <= self.daily_stop_pct <= 30:
            raise ValueError("单日熔断需要在 0.5% 到 30% 之间")
        if not 1 <= self.gap_pct <= 30:
            raise ValueError("跳空阈值需要在 1% 到 30% 之间")
        if not 0 <= self.fee_rate <= 0.01:
            raise ValueError("费率需要在 0 到 1% 之间")
        self.min_score = int(self.min_score)


def default_config(mode: str) -> SimConfig:
    mode = mode.lower()
    if mode == "index":
        return SimConfig(
            mode="index",
            symbol="000300",
            style="trend",
            initial_cash=100_000.0,
            ma_fast=20,
            ma_slow=60,
            breakout_lookback=20,
            min_breakout_pct=0.0,
            pullback_pct=3.0,
            max_atr_pct=4.5,
            max_extension_pct=14.0,
            min_rel_volume=0.6,
            min_score=55,
            position_size_pct=40.0,
            stop_loss_pct=8.0,
            tp1_gain_pct=10.0,
            tp2_gain_pct=20.0,
            trail_pct=8.0,
            daily_stop_pct=4.0,
            gap_pct=6.0,
            fee_rate=0.0002,
            lookback=480,
        )
    if mode == "stock":
        return SimConfig(
            mode="stock",
            symbol="600519",
            style="breakout",
            initial_cash=100_000.0,
            ma_fast=20,
            ma_slow=60,
            breakout_lookback=20,
            min_breakout_pct=0.0,
            pullback_pct=4.0,
            max_atr_pct=8.0,
            max_extension_pct=18.0,
            min_rel_volume=1.0,
            min_score=60,
            position_size_pct=20.0,
            stop_loss_pct=8.0,
            tp1_gain_pct=12.0,
            tp2_gain_pct=25.0,
            trail_pct=10.0,
            daily_stop_pct=5.0,
            gap_pct=6.0,
            fee_rate=0.0003,
            lookback=480,
        )
    if mode == "crypto":
        return SimConfig(
            mode="crypto",
            symbol="BTCUSDT",
            style="breakout",
            initial_cash=10_000.0,
            ma_fast=10,
            ma_slow=30,
            breakout_lookback=20,
            min_breakout_pct=0.0,
            pullback_pct=5.0,
            max_atr_pct=12.0,
            max_extension_pct=28.0,
            min_rel_volume=0.5,
            min_score=55,
            position_size_pct=20.0,
            stop_loss_pct=12.0,
            tp1_gain_pct=20.0,
            tp2_gain_pct=45.0,
            trail_pct=15.0,
            daily_stop_pct=8.0,
            gap_pct=8.0,
            fee_rate=0.001,
            lookback=480,
        )
    raise ValueError("mode 只能是 crypto、index 或 stock")


def bar_defaults_payload() -> Dict[str, Dict[str, Any]]:
    payload = {}
    for mode in ("crypto", "index", "stock"):
        cfg = default_config(mode)
        data = asdict(cfg)
        data["initial_balance"] = cfg.initial_cash
        data["rule5_min_score"] = cfg.min_score
        data["rule6_position_size_pct"] = cfg.position_size_pct
        data["rule6_stop_loss_pct"] = cfg.stop_loss_pct
        payload[mode] = data
    return payload


def apply_prompt(cfg: SimConfig, text: str) -> SimConfig:
    if not text or not text.strip():
        return cfg
    if re.search(r"均值回归|mean[\s-]?reversion", text, re.I):
        cfg.style = "mean_reversion"
    elif re.search(r"突破|breakout", text, re.I):
        cfg.style = "breakout"
    elif re.search(r"趋势|trend", text, re.I):
        cfg.style = "trend"

    matched = re.search(r"MA\s*(\d+)\s*/\s*(\d+)", text, re.I)
    if not matched:
        matched = re.search(r"(\d+)\s*日\s*/\s*(\d+)\s*日", text)
    if matched:
        cfg.ma_fast = int(matched.group(1))
        cfg.ma_slow = int(matched.group(2))
    matched = re.search(r"快(?:均)?线\s*(\d+)", text)
    if matched:
        cfg.ma_fast = int(matched.group(1))
    matched = re.search(r"慢(?:均)?线\s*(\d+)", text)
    if matched:
        cfg.ma_slow = int(matched.group(1))
    matched = re.search(r"突破\s*(\d+)\s*日", text)
    if matched:
        cfg.breakout_lookback = int(matched.group(1))
    matched = re.search(r"仓位\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.position_size_pct = float(matched.group(1))
    matched = re.search(r"(?:硬)?止损\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.stop_loss_pct = float(matched.group(1))
    matched = re.search(r"移动止盈\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.trail_pct = float(matched.group(1))
    matched = re.search(r"(?<!移动)止盈\s*(\d+(?:\.\d+)?)\s*%\s*[/／]\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.tp1_gain_pct = float(matched.group(1))
        cfg.tp2_gain_pct = float(matched.group(2))
    matched = re.search(r"评分\s*(\d+)", text)
    if matched:
        cfg.min_score = int(matched.group(1))
    matched = re.search(r"偏离(?:上限)?\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.max_extension_pct = float(matched.group(1))
    matched = re.search(r"波动(?:上限)?\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.max_atr_pct = float(matched.group(1))
    matched = re.search(r"熔断\s*(\d+(?:\.\d+)?)\s*%", text)
    if matched:
        cfg.daily_stop_pct = float(matched.group(1))
    matched = re.search(r"近\s*(\d+)\s*根", text)
    if matched:
        cfg.lookback = int(matched.group(1))
    return cfg


def _fmt(value: float) -> str:
    return f"{value:g}"


def rules_for(cfg: SimConfig) -> List[Dict[str, str]]:
    fast = cfg.ma_fast
    slow = cfg.ma_slow
    if cfg.mode == "index":
        if cfg.style == "breakout":
            r1 = f"1. 收盘未站上 {slow} 日均线则跳过。宽基只做多。"
            r2 = f"2. 未突破近 {cfg.breakout_lookback} 日高点（超出 {_fmt(cfg.min_breakout_pct)}%）则跳过。"
            r7 = f"7. 收盘跌破 {slow} 日均线，平掉剩余仓位。"
        elif cfg.style == "mean_reversion":
            r1 = f"1. {slow} 日均线 5 日内下行超过 2%，或已离开趋势带，则跳过。"
            r2 = f"2. 收盘相对 {fast} 日均线的回撤不足 {_fmt(cfg.pullback_pct)}% 则跳过。"
            r7 = f"7. 收盘回到 {fast} 日均线，均值回归完成，平掉剩余仓位。"
        else:
            r1 = f"1. 收盘未站上 {slow} 日均线则跳过。宽基只做多。"
            r2 = f"2. {fast} 日均线未站上 {slow} 日均线则跳过。"
            r7 = f"7. 收盘跌破 {slow} 日均线，平掉剩余仓位。"
        r4 = (
            f"4. 收盘跌破 {slow} 日均线则否决，回归前提不成立。"
            if cfg.style == "mean_reversion"
            else f"4. 收盘高于 {fast} 日均线超过 {_fmt(cfg.max_extension_pct)}% 则否决，避免追高。"
        )
        return [
            {"id": "R1", "agent": "Finder", "text": r1},
            {"id": "R2", "agent": "Finder", "text": r2},
            {"id": "R3", "agent": "Auditor", "text": f"3. ATR(14)/收盘超过 {_fmt(cfg.max_atr_pct)}% 则否决。"},
            {"id": "R4", "agent": "Auditor", "text": r4},
            {"id": "R5", "agent": "Judge", "text": f"5. 趋势、波动与量能评分低于 {cfg.min_score} 则放弃。"},
            {
                "id": "R6",
                "agent": "Stager",
                "text": f"6. 按权益的 {_fmt(cfg.position_size_pct)}% 建仓。硬止损 {_fmt(cfg.stop_loss_pct)}% 在成交前写好，不下移。",
            },
            {"id": "R7", "agent": "Closer", "text": r7},
            {
                "id": "EXIT",
                "agent": "Closer",
                "text": (
                    f"出场：+{_fmt(cfg.tp1_gain_pct)}% 减仓一半，+{_fmt(cfg.tp2_gain_pct)}% 再减原仓四分之一，"
                    f"其余从高点回撤 {_fmt(cfg.trail_pct)}% 离场。单日权益回撤 {_fmt(cfg.daily_stop_pct)}% 停止新开仓。"
                ),
            },
        ]

    if cfg.mode == "stock":
        if cfg.style == "trend":
            r2 = f"2. {fast} 日均线未站上 {slow} 日均线，或收盘跌破 {slow} 日均线，则跳过。"
        elif cfg.style == "mean_reversion":
            r2 = f"2. 相对 {fast} 日均线回撤不足 {_fmt(cfg.pullback_pct)}%，或已跌破 {slow} 日均线，则跳过。"
        else:
            r2 = f"2. 未突破近 {cfg.breakout_lookback} 日高点，或收盘跌破 {slow} 日均线，则跳过。"
        r7 = f"7. 低开超过 {_fmt(cfg.gap_pct)}% 则平仓。"
        r4 = f"4. 前一日跳空高开超过 {_fmt(cfg.gap_pct)}% 且当日回落吞没缺口，或偏离 {fast} 日均线超过 {_fmt(cfg.max_extension_pct)}%，则否决。"
        r1 = f"1. 成交量低于近 20 日均量的 {_fmt(cfg.min_rel_volume)} 倍则跳过。"
    else:
        if cfg.style == "trend":
            r2 = f"2. {fast} 日均线未站上 {slow} 日均线，或收盘跌破 {slow} 日均线，则跳过。"
        elif cfg.style == "mean_reversion":
            r2 = f"2. 相对 {fast} 日均线回撤不足 {_fmt(cfg.pullback_pct)}%，或已跌破 {slow} 日均线，则跳过。"
        else:
            r2 = f"2. 未突破近 {cfg.breakout_lookback} 日高点，或收盘跌破 {slow} 日均线，则跳过。"
        r7 = "7. 振幅异常放大且收阴，视为波动冲击，平掉剩余仓位。样本不含链上转账。"
        r4 = f"4. 收盘高于 {fast} 日均线超过 {_fmt(cfg.max_extension_pct)}% 则否决。"
        r1 = f"1. 成交额低于近 20 日中位数的 {_fmt(cfg.min_rel_volume)} 倍则跳过。"
    return [
        {"id": "R1", "agent": "Finder", "text": r1},
        {"id": "R2", "agent": "Finder", "text": r2},
        {"id": "R3", "agent": "Auditor", "text": f"3. ATR(14)/收盘超过 {_fmt(cfg.max_atr_pct)}% 则否决。"},
        {"id": "R4", "agent": "Auditor", "text": r4},
        {"id": "R5", "agent": "Judge", "text": f"5. 量价与均线评分低于 {cfg.min_score} 则放弃。"},
        {
            "id": "R6",
            "agent": "Stager",
            "text": f"6. 按权益的 {_fmt(cfg.position_size_pct)}% 建仓。硬止损 {_fmt(cfg.stop_loss_pct)}% 在成交前写好，不下移。",
        },
        {"id": "R7", "agent": "Closer", "text": r7},
        {
            "id": "EXIT",
            "agent": "Closer",
            "text": (
                f"出场：+{_fmt(cfg.tp1_gain_pct)}% 减仓一半，+{_fmt(cfg.tp2_gain_pct)}% 再减原仓四分之一，"
                f"其余从高点回撤 {_fmt(cfg.trail_pct)}% 离场。单日权益回撤 {_fmt(cfg.daily_stop_pct)}% 停止新开仓。"
            ),
        },
    ]


def _sma(values: Sequence[float], end: int, window: int) -> Optional[float]:
    if end + 1 < window:
        return None
    chunk = values[end + 1 - window : end + 1]
    return sum(chunk) / window


def _atr_pct(bars: Sequence[Bar], end: int, window: int = 14) -> Optional[float]:
    if end < window:
        return None
    total = 0.0
    for index in range(end - window + 1, end + 1):
        prev_close = bars[index - 1].close if index else bars[index].close
        bar = bars[index]
        true_range = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        total += true_range
    close = bars[end].close
    if close <= 0:
        return None
    return (total / window) / close * 100.0


def _rel_activity(bars: Sequence[Bar], end: int, mode: str) -> float:
    start = end - 20
    if start < 0:
        return 1.0
    if mode == "crypto":
        history = [bars[index].close * bars[index].volume for index in range(start, end)]
        today = bars[end].close * bars[end].volume
        ordered = sorted(history)
        baseline = ordered[len(ordered) // 2]
    else:
        history = [bars[index].volume for index in range(start, end)]
        today = bars[end].volume
        baseline = sum(history) / len(history)
    if baseline <= 0:
        return 1.0
    return today / baseline


def _prior_high(bars: Sequence[Bar], end: int, window: int) -> float:
    return max(bars[index].high for index in range(end - window, end))


def _score(close: float, sma_fast: float, sma_slow: float, atr_pct: float, rel: float, broke: bool, cfg: SimConfig) -> tuple[int, str]:
    points = 0
    parts: List[str] = []
    if close > sma_slow:
        points += 25
        parts.append("站上慢均线")
    if sma_fast > sma_slow:
        points += 20
        parts.append("均线多头")
    if atr_pct <= cfg.max_atr_pct:
        points += 15
        parts.append("波动可接受")
    if cfg.style == "breakout" and broke:
        points += 25
        parts.append("突破成立")
    elif cfg.style == "trend" and close > sma_fast:
        points += 25
        parts.append("价格在快均线之上")
    elif cfg.style == "mean_reversion" and close < sma_fast:
        points += 25
        parts.append("回撤到快均线下方")
    if rel >= 1:
        points += 15
        parts.append("量能不弱")
    else:
        points += 5
    return min(100, points), "、".join(parts) if parts else "无加分项"


def _block(action: str, agent: str, rule_id: str, reason: str) -> Dict[str, Any]:
    return {"ok": False, "action": action, "agent": agent, "rule_id": rule_id, "reason": reason, "score": 0}


def evaluate_signal(bars: Sequence[Bar], index: int, cfg: SimConfig) -> Dict[str, Any]:
    closes = [bar.close for bar in bars]
    sma_fast = _sma(closes, index, cfg.ma_fast)
    sma_slow = _sma(closes, index, cfg.ma_slow)
    sma_slow_prev = _sma(closes, index - 5, cfg.ma_slow)
    atr_pct = _atr_pct(bars, index)
    if sma_fast is None or sma_slow is None or atr_pct is None or sma_slow_prev is None:
        return _block("warmup", "", "", "均线尚未就绪")

    close = bars[index].close
    rel = _rel_activity(bars, index, cfg.mode)
    prior_high = _prior_high(bars, index, cfg.breakout_lookback)
    threshold = prior_high * (1.0 + cfg.min_breakout_pct / 100.0)
    broke = close >= threshold
    extension = (close / sma_fast - 1.0) * 100.0

    if cfg.mode in {"stock", "crypto"}:
        if rel < cfg.min_rel_volume:
            if cfg.mode == "crypto":
                reason = f"成交额仅为近20日中位数的 {rel:.2f} 倍，低于 {_fmt(cfg.min_rel_volume)} 倍"
            else:
                reason = f"相对成交量 {rel:.2f} 倍，低于 {_fmt(cfg.min_rel_volume)} 倍"
            return _block("skip", "Finder", "R1", reason)

    if cfg.mode == "index" and cfg.style == "trend":
        if close <= sma_slow:
            return _block("skip", "Finder", "R1", f"收盘 {close:.2f} 未站上慢均线 {sma_slow:.2f}")
        if sma_fast <= sma_slow:
            return _block("skip", "Finder", "R2", f"快均线 {sma_fast:.2f} 未站上慢均线 {sma_slow:.2f}")
    elif cfg.mode == "index" and cfg.style == "breakout":
        if close <= sma_slow:
            return _block("skip", "Finder", "R1", f"收盘 {close:.2f} 未站上慢均线 {sma_slow:.2f}")
        if not broke:
            return _block("skip", "Finder", "R2", f"未突破近{cfg.breakout_lookback}日高点 {prior_high:.2f}")
    elif cfg.mode == "index" and cfg.style == "mean_reversion":
        if sma_slow < sma_slow_prev * 0.98:
            return _block("skip", "Finder", "R1", "慢均线下行过快，暂停均值回归")
        if close > sma_fast * (1.0 - cfg.pullback_pct / 100.0):
            return _block("skip", "Finder", "R2", f"相对快均线回撤不足 {_fmt(cfg.pullback_pct)}%")
    elif cfg.style == "trend":
        if sma_fast <= sma_slow or close <= sma_slow:
            return _block("skip", "Finder", "R2", f"均线未共振：快 {sma_fast:.2f} / 慢 {sma_slow:.2f}")
    elif cfg.style == "mean_reversion":
        if close <= sma_slow or close > sma_fast * (1.0 - cfg.pullback_pct / 100.0):
            return _block("skip", "Finder", "R2", "不在慢均线之上的回撤区间")
    else:
        if not broke or close <= sma_slow:
            why = "跌破慢均线" if close <= sma_slow else f"未突破近{cfg.breakout_lookback}日高点 {prior_high:.2f}"
            return _block("skip", "Finder", "R2", why)

    if atr_pct > cfg.max_atr_pct:
        return _block("kill", "Auditor", "R3", f"ATR% {atr_pct:.2f} 超过上限 {_fmt(cfg.max_atr_pct)}")

    if cfg.mode == "stock" and index >= 2:
        prev = bars[index - 1]
        older_close = bars[index - 2].close
        prev_gap = prev.open / older_close - 1.0
        if prev_gap >= cfg.gap_pct / 100.0 and close < prev.open:
            return _block("kill", "Auditor", "R4", f"前一日跳空 {prev_gap * 100:.1f}% 后回落，缺口失败")

    if cfg.style == "mean_reversion":
        if close < sma_slow:
            return _block("kill", "Auditor", "R4", f"收盘 {close:.2f} 跌破慢均线 {sma_slow:.2f}")
    elif extension > cfg.max_extension_pct:
        return _block("kill", "Auditor", "R4", f"偏离快均线 {extension:.1f}%，超过 {_fmt(cfg.max_extension_pct)}%")

    score, parts = _score(close, sma_fast, sma_slow, atr_pct, rel, broke, cfg)
    if score < cfg.min_score:
        return _block("reject", "Judge", "R5", f"评分 {score}，低于 {cfg.min_score}（{parts}）")
    return {
        "ok": True,
        "action": "signal",
        "agent": "Judge",
        "rule_id": "R5",
        "reason": f"评分 {score}（{parts}）。信号在收盘确认，次日开盘建仓。",
        "score": score,
    }


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


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
    returns = []
    for index in range(1, len(equity)):
        if equity[index - 1] > 0:
            returns.append(equity[index] / equity[index - 1] - 1.0)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    if var <= 0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(252.0)


def run_symbol_backtest(bars: Sequence[Bar], cfg: SimConfig) -> Dict[str, Any]:
    cfg.validate()
    if len(bars) < cfg.ma_slow + 20:
        raise ValueError(f"K线只有 {len(bars)} 根，慢均线 {cfg.ma_slow} 无法形成")
    needed = max(cfg.ma_slow + 6, cfg.breakout_lookback + 1, 21)
    if len(bars) <= needed + 10:
        raise ValueError(f"可用K线不足，至少需要 {needed + 10} 根，当前 {len(bars)}")
    start = max(needed, len(bars) - cfg.lookback)
    # Indicators read bars before `start`; the loop index is on the full series.

    cash = float(cfg.initial_cash)
    qty = 0.0
    avg = 0.0
    hard_stop = 0.0
    high_water = 0.0
    tp1_done = False
    tp2_done = False
    pending: Optional[Dict[str, Any]] = None
    halt_until = -1
    cash_at_entry = 0.0
    entry_notional = 0.0
    entry_date = ""
    entry_price = 0.0
    original_qty = 0.0
    sell_notional = 0.0
    sell_qty = 0.0

    events: List[Dict[str, Any]] = []
    markers: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    funnel = {
        "bars": 0,
        "finder_skips": 0,
        "auditor_kills": 0,
        "judge_rejects": 0,
        "signals": 0,
        "entries": 0,
        "exits": 0,
        "scale_outs": 0,
    }

    def mark(side: str, price: float, rule_id: str, reason: str, event_index: int, day: str) -> None:
        markers.append(
            {
                "i": event_index,
                "date": day,
                "side": side,
                "price": _round(price, 6),
                "rule_id": rule_id,
                "reason": reason,
            }
        )

    def sell(size: float, price: float) -> float:
        nonlocal cash, qty, sell_notional, sell_qty
        size = min(size, qty)
        if size <= 0 or price <= 0:
            return 0.0
        fee = size * price * cfg.fee_rate
        cash += size * price - fee
        qty -= size
        sell_notional += size * price
        sell_qty += size
        if qty <= 1e-10:
            qty = 0.0
        return size

    last_index = len(bars) - 1
    for index in range(start, len(bars)):
        bar = bars[index]
        prev = bars[index - 1]
        prev_equity = cash + qty * prev.close
        entered = False
        scaled = False
        exited = False
        exit_reason = ""
        exit_rule = ""
        exit_fill = bar.close
        entry_reason = ""
        scale_price = avg
        decision: Optional[Dict[str, Any]] = None
        primary_reason_bits: List[str] = []
        cash_rejected = False

        if pending and qty == 0:
            equity_ref = cash
            budget = equity_ref * cfg.position_size_pct / 100.0
            notional = min(budget, cash / (1.0 + cfg.fee_rate) if cfg.fee_rate > -0.999 else cash)
            fill = bar.open
            if notional >= 1 and fill > 0:
                buy_qty = notional / fill
                fee = buy_qty * fill * cfg.fee_rate
                cash -= buy_qty * fill + fee
                qty = buy_qty
                avg = fill
                hard_stop = fill * (1.0 - cfg.stop_loss_pct / 100.0)
                high_water = fill
                tp1_done = False
                tp2_done = False
                cash_at_entry = cash + buy_qty * fill + fee
                entry_notional = buy_qty * fill
                entry_date = bar.date
                entry_price = fill
                original_qty = buy_qty
                sell_notional = 0.0
                sell_qty = 0.0
                entered = True
                entry_reason = f"开盘 {fill:.4g} 建仓 {cfg.position_size_pct:g}% ，硬止损 {hard_stop:.4g}"
                funnel["entries"] += 1
            else:
                cash_rejected = True
            pending = None

        if qty > 0:
            protective_trail = high_water * (1.0 - cfg.trail_pct / 100.0)
            stop_price = None
            stop_rule = ""
            stop_reason = ""
            gap_down = cfg.mode == "stock" and bar.open <= prev.close * (1.0 - cfg.gap_pct / 100.0)
            if gap_down:
                stop_price = bar.open
                stop_rule = "R7"
                stop_reason = f"低开超过 {_fmt(cfg.gap_pct)}%，Closer 平仓"
            elif bar.open <= hard_stop:
                stop_price = bar.open
                stop_rule = "R6"
                stop_reason = f"开盘跌破硬止损 {hard_stop:.4g}"
            elif not entered and bar.open <= protective_trail:
                stop_price = bar.open
                stop_rule = "EXIT"
                stop_reason = f"开盘跌破移动止盈 {protective_trail:.4g}"
            elif bar.low <= hard_stop:
                stop_price = hard_stop
                stop_rule = "R6"
                stop_reason = f"触及硬止损 {hard_stop:.4g}"
            elif not entered and bar.low <= protective_trail:
                stop_price = protective_trail
                stop_rule = "EXIT"
                stop_reason = f"触及移动止盈 {protective_trail:.4g}"

            if stop_price is not None:
                sell(qty, stop_price)
                exited = True
                exit_fill = stop_price
                exit_rule = stop_rule
                exit_reason = stop_reason
            else:
                tp1_price = avg * (1.0 + cfg.tp1_gain_pct / 100.0)
                tp2_price = avg * (1.0 + cfg.tp2_gain_pct / 100.0)
                if not tp1_done and bar.high >= tp1_price:
                    px = bar.open if bar.open >= tp1_price else tp1_price
                    sold = sell(original_qty * 0.5, px)
                    if sold > 0:
                        tp1_done = True
                        scaled = True
                        scale_price = px
                        funnel["scale_outs"] += 1
                        primary_reason_bits.append(f"+{_fmt(cfg.tp1_gain_pct)}% 减仓一半 @ {px:.4g}")
                if qty > 0 and not tp2_done and bar.high >= tp2_price:
                    px = bar.open if bar.open >= tp2_price else tp2_price
                    sold = sell(original_qty * 0.25, px)
                    if sold > 0:
                        tp2_done = True
                        scaled = True
                        scale_price = px
                        funnel["scale_outs"] += 1
                        primary_reason_bits.append(f"+{_fmt(cfg.tp2_gain_pct)}% 再减四分之一 @ {px:.4g}")

                if qty > 0 and index == last_index:
                    sell(qty, bar.close)
                    exited = True
                    exit_fill = bar.close
                    exit_rule = "EXIT"
                    exit_reason = "样本结束，按收盘价平仓"
                elif qty > 0:
                    closes = [item.close for item in bars]
                    sma_fast = _sma(closes, index, cfg.ma_fast)
                    sma_slow = _sma(closes, index, cfg.ma_slow)
                    shock = (bar.high - bar.low) / bar.close * 100.0 if bar.close else 0.0
                    if cfg.mode == "index" and cfg.style == "mean_reversion" and sma_fast and bar.close >= sma_fast:
                        sell(qty, bar.close)
                        exited = True
                        exit_fill = bar.close
                        exit_rule = "R7"
                        exit_reason = f"收盘回到快均线 {sma_fast:.2f}"
                    elif cfg.mode == "index" and cfg.style != "mean_reversion" and sma_slow and bar.close < sma_slow:
                        sell(qty, bar.close)
                        exited = True
                        exit_fill = bar.close
                        exit_rule = "R7"
                        exit_reason = f"收盘跌破慢均线 {sma_slow:.2f}"
                    elif cfg.mode == "crypto" and shock >= cfg.max_atr_pct * 1.5 and bar.close < bar.open:
                        sell(qty, bar.close)
                        exited = True
                        exit_fill = bar.close
                        exit_rule = "R7"
                        exit_reason = f"振幅 {shock:.1f}% 且收阴，波动冲击平仓"
                    elif cfg.style == "mean_reversion" and cfg.mode != "index" and sma_fast and bar.close >= sma_fast:
                        sell(qty, bar.close)
                        exited = True
                        exit_fill = bar.close
                        exit_rule = "R7"
                        exit_reason = f"收盘回到快均线 {sma_fast:.2f}"

            if qty > 0:
                high_water = max(high_water, bar.high)

        equity_close = cash + qty * bar.close
        if prev_equity > 0 and (equity_close / prev_equity - 1.0) * 100.0 <= -cfg.daily_stop_pct:
            if qty > 0:
                sell(qty, bar.close)
                exited = True
                exit_fill = bar.close
                exit_rule = "DAILY"
                exit_reason = f"单日权益回撤达到 {_fmt(cfg.daily_stop_pct)}% 熔断"
            halt_until = index + 1

        forced = exit_reason.startswith("样本结束")
        if qty == 0 and entry_date and (exited or scaled):
            pnl = cash - cash_at_entry
            exit_price = sell_notional / sell_qty if sell_qty else bar.close
            trades.append(
                {
                    "entry_date": entry_date,
                    "exit_date": bar.date,
                    "entry_price": _round(entry_price, 6),
                    "exit_price": _round(exit_price, 6),
                    "qty": _round(original_qty, 8),
                    "pnl": _round(pnl, 4),
                    "pnl_pct": _round(pnl / entry_notional * 100.0, 4) if entry_notional else 0.0,
                    "reason": exit_reason or "平仓",
                    "rule_id": exit_rule or "EXIT",
                    "forced": forced,
                    "win": pnl > 1e-6,
                }
            )
            funnel["exits"] += 1
            entry_date = ""

        if qty == 0 and not exited and index < last_index and index >= halt_until:
            decision = evaluate_signal(bars, index, cfg)
            if decision["ok"]:
                pending = decision
                funnel["signals"] += 1
            elif decision["action"] == "skip":
                funnel["finder_skips"] += 1
            elif decision["action"] == "kill":
                funnel["auditor_kills"] += 1
            elif decision["action"] == "reject":
                funnel["judge_rejects"] += 1

        event_index = len(events)
        if entered:
            mark("buy", bar.open, "R6", entry_reason, event_index, bar.date)
        if scaled:
            mark("scale", scale_price or avg, "EXIT", "分批止盈", event_index, bar.date)
        if exited:
            mark("sell", exit_fill, exit_rule, exit_reason, event_index, bar.date)

        if exited:
            action = "exit"
            agent = "Closer"
            rule_id = exit_rule or "EXIT"
            reason = exit_reason
            if entered:
                reason = entry_reason + "；随后" + exit_reason
            fired = [rule_id]
        elif scaled:
            action = "scale_out"
            agent = "Closer"
            rule_id = "EXIT"
            reason = "；".join(primary_reason_bits) or "分批止盈"
            if entered:
                reason = entry_reason + "；" + reason
            fired = ["EXIT"]
        elif entered:
            action = "entry"
            agent = "Stager"
            rule_id = "R6"
            reason = entry_reason
            fired = ["R6"]
        elif cash_rejected:
            action = "reject"
            agent = "Stager"
            rule_id = "R6"
            reason = "现金不足以按规则建仓"
            fired = ["R6"]
        elif decision and decision["ok"]:
            action = "signal"
            agent = "Judge"
            rule_id = "R5"
            reason = decision["reason"]
            fired = ["R5"]
        elif decision and not decision["ok"] and decision["action"] != "warmup":
            action = decision["action"]
            agent = decision["agent"]
            rule_id = decision["rule_id"]
            reason = decision["reason"]
            fired = [rule_id] if rule_id else []
        elif index < halt_until and qty == 0:
            action = "halt"
            agent = "Closer"
            rule_id = "DAILY"
            reason = "熔断后的下一根K线不再开新仓"
            fired = ["DAILY"]
        elif qty > 0:
            action = "hold"
            agent = "Closer"
            rule_id = "EXIT"
            reason = f"持仓 {qty:.6g} @ {avg:.4g}，止损 {hard_stop:.4g}"
            fired = []
        else:
            action = "hold"
            agent = ""
            rule_id = ""
            reason = "样本末端，不再发单" if index == last_index else "空仓观望"
            fired = []

        if action not in EVENT_ACTIONS:
            raise RuntimeError(f"unexpected action {action}")

        position_value = qty * bar.close
        equity = cash + position_value
        events.append(
            {
                "i": event_index,
                "date": bar.date,
                "open": _round(bar.open, 6),
                "high": _round(bar.high, 6),
                "low": _round(bar.low, 6),
                "close": _round(bar.close, 6),
                "volume": _round(bar.volume, 4),
                "equity": _round(equity, 4),
                "cash": _round(cash, 4),
                "position_qty": _round(qty, 8),
                "position_value": _round(position_value, 4),
                "avg_price": _round(avg, 6) if qty else 0.0,
                "agent": agent,
                "rule_id": rule_id,
                "action": action,
                "reason": reason,
                "fired_rules": fired,
                "benchmark_equity": 0.0,
            }
        )
        funnel["bars"] += 1

    base_close = events[0]["close"]
    equities = [cfg.initial_cash]
    for event in events:
        bench = cfg.initial_cash * event["close"] / base_close if base_close else cfg.initial_cash
        event["benchmark_equity"] = _round(bench, 4)
        equities.append(event["equity"])

    final_equity = events[-1]["equity"]
    wins = sum(1 for trade in trades if trade["win"])
    losses = len(trades) - wins
    total_return = (final_equity / cfg.initial_cash - 1.0) * 100.0 if cfg.initial_cash else 0.0
    bench_return = (events[-1]["close"] / base_close - 1.0) * 100.0 if base_close else 0.0
    drawdown = _max_drawdown(equities) * 100.0
    win_rate = wins / len(trades) if trades else 0.0
    summary = {
        "initial_equity": _round(cfg.initial_cash, 4),
        "final_equity": _round(final_equity, 4),
        "total_return_pct": _round(total_return, 4),
        "benchmark_return_pct": _round(bench_return, 4),
        "max_drawdown_pct": _round(drawdown, 4),
        "trade_count": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": _round(win_rate, 4),
        "sharpe": _round(_sharpe([event["equity"] for event in events]), 4),
        "avg_trade_pct": _round(sum(trade["pnl_pct"] for trade in trades) / len(trades), 4) if trades else 0.0,
    }
    return {
        "engine": "bar",
        "mode": cfg.mode,
        "symbol": cfg.symbol,
        "style": cfg.style,
        "currency": "CNY" if cfg.mode != "crypto" else "USDT",
        "initial_balance": _round(cfg.initial_cash, 4),
        "final_balance": summary["final_equity"],
        "total_return_pct": summary["total_return_pct"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "trade_count": summary["trade_count"],
        "win_rate": summary["win_rate"],
        "wins": wins,
        "losses": losses,
        "sharpe": summary["sharpe"],
        "benchmark_return_pct": summary["benchmark_return_pct"],
        "rules": rules_for(cfg),
        "funnel": funnel,
        "summary": summary,
        "trades": trades,
        "markers": markers,
        "events": events,
        "config": asdict(cfg),
        "assumptions": "信号在收盘确认，下一根开盘成交。同一根K线同时触及止损和止盈时，按止损成交。权益按收盘价盯市。",
    }


def config_from_fields(mode: str, symbol: str, fields: Dict[str, Any]) -> SimConfig:
    cfg = default_config(mode)
    cfg.symbol = symbol or cfg.symbol
    renamed = {
        "initial_balance": "initial_cash",
        "initial_cash": "initial_cash",
        "lookback": "lookback",
        "style": "style",
        "ma_fast": "ma_fast",
        "ma_slow": "ma_slow",
        "breakout_lookback": "breakout_lookback",
        "min_breakout_pct": "min_breakout_pct",
        "pullback_pct": "pullback_pct",
        "max_atr_pct": "max_atr_pct",
        "max_extension_pct": "max_extension_pct",
        "min_rel_volume": "min_rel_volume",
        "min_score": "min_score",
        "rule5_min_score": "min_score",
        "position_size_pct": "position_size_pct",
        "rule6_position_size_pct": "position_size_pct",
        "stop_loss_pct": "stop_loss_pct",
        "rule6_stop_loss_pct": "stop_loss_pct",
        "tp1_gain_pct": "tp1_gain_pct",
        "tp2_gain_pct": "tp2_gain_pct",
        "trail_pct": "trail_pct",
        "daily_stop_pct": "daily_stop_pct",
        "gap_pct": "gap_pct",
        "fee_rate": "fee_rate",
    }
    for source, target in renamed.items():
        value = fields.get(source)
        if value is not None:
            setattr(cfg, target, value)
    prompt = fields.get("prompt_text") or ""
    if prompt:
        apply_prompt(cfg, str(prompt))
    cfg.validate()
    return cfg
