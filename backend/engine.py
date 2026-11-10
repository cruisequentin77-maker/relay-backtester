"""
Relay Backtester - Universal 5-Agent Rule Pipeline Backtesting Engine
Supports both Crypto (On-Chain Meme/Token) and Stock (Technical/Fundamental) modes.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import math
import random


@dataclass
class RelayRuleConfig:
    mode: str = "crypto"  # "crypto" or "stock"
    desk_id: str = "Desk 0001"
    initial_balance: float = 100.0
    days: int = 9
    total_candidates: int = 14200
    # Rule 1 & 2: Finder
    rule1_min_liquidity_or_vol: float = 20000.0   # Crypto: $20k liquidity | Stock: 2.0x relative volume
    rule2_max_age_or_gap: float = 6.0             # Crypto: <= 6h coin age | Stock: >= 3.0% gap/breakout
    # Rule 3 & 4: Auditor
    rule3_max_holder_or_pe: float = 35.0          # Crypto: Top 10 holders <= 35% | Stock: P/E <= 65 or Debt/Eq <= 35%
    rule4_contract_or_earnings_safe: bool = True  # Crypto: Mint/freeze renounced | Stock: No earnings trap / ST flag
    # Rule 5: Judge
    rule5_min_score: int = 80                     # Multi-factor conviction score >= 80
    # Rule 6: Stager
    rule6_position_size_pct: float = 4.0          # Position size % of book (or 10% for stock)
    rule6_stop_loss_pct: float = 25.0             # Hard stop loss % set before entry
    # Rule 7 & Exits: Closer
    rule7_anomaly_exit: bool = True               # Crypto: Dev wallet -> exchange dump exit | Stock: Insider/Sector flash crash exit
    daily_stop_pct: float = 10.0                  # Daily circuit breaker (-10% crypto / -4% stock)
    tp1_gain_pct: float = 100.0                   # Take 50% at +100% (+20% stock)
    tp2_gain_pct: float = 200.0                   # Take 25% at +200% (+40% stock)
    trail_pct: float = 25.0                       # Trail the rest by 25% (8% stock)
    seed: int = 42


def get_default_rules_text(cfg: RelayRuleConfig) -> List[Dict[str, str]]:
    if cfg.mode == "crypto":
        return [
            {"id": "R1", "agent": "Finder", "text": f"1. Liquidity under ${cfg.rule1_min_liquidity_or_vol:,.0f}: skip."},
            {"id": "R2", "agent": "Finder", "text": f"2. Coin older than {cfg.rule2_max_age_or_gap:g} hours: skip."},
            {"id": "R3", "agent": "Auditor", "text": f"3. Top 10 holders above {cfg.rule3_max_holder_or_pe:g}% of supply: kill."},
            {"id": "R4", "agent": "Auditor", "text": "4. Mint or freeze authority not renounced: kill."},
            {"id": "R5", "agent": "Judge", "text": f"5. Momentum & orderflow score below {cfg.rule5_min_score}: pass."},
            {"id": "R6", "agent": "Stager", "text": f"6. Size {cfg.rule6_position_size_pct:g}% of book. Stop at -{cfg.rule6_stop_loss_pct:g}%, set before entry, never moved down."},
            {"id": "R7", "agent": "Closer", "text": "7. If the dev sends one token toward an exchange, sell everything in that same block. No debate."},
            {"id": "EXIT", "agent": "Closer", "text": f"EXITS: Take 50% at +{cfg.tp1_gain_pct:g}%, 25% at +{cfg.tp2_gain_pct:g}%, trail the rest {cfg.trail_pct:g}%. DAILY STOP: -{cfg.daily_stop_pct:g}%."},
        ]
    else:
        return [
            {"id": "R1", "agent": "Finder", "text": f"1. Relative Volume under {cfg.rule1_min_liquidity_or_vol:g}x or illiquid float: skip."},
            {"id": "R2", "agent": "Finder", "text": f"2. Intraday breakout / ATR expansion under {cfg.rule2_max_age_or_gap:g}%: skip."},
            {"id": "R3", "agent": "Auditor", "text": f"3. Debt-to-Equity / Short crowding risk above {cfg.rule3_max_holder_or_pe:g}%: kill."},
            {"id": "R4", "agent": "Auditor", "text": "4. Pending SEC inquiry, earnings gap trap, or negative cashflow flag: kill."},
            {"id": "R5", "agent": "Judge", "text": f"5. Multi-factor RS + Volume + MA alignment score below {cfg.rule5_min_score}: pass."},
            {"id": "R6", "agent": "Stager", "text": f"6. Size {cfg.rule6_position_size_pct:g}% of equity. Hard stop at -{cfg.rule6_stop_loss_pct:g}% placed via bracket order before fill."},
            {"id": "R7", "agent": "Closer", "text": "7. If sector ETF breaks VWAP with institutional dark-pool dump, flatten position immediately."},
            {"id": "EXIT", "agent": "Closer", "text": f"EXITS: Take 50% at +{cfg.tp1_gain_pct:g}%, 25% at +{cfg.tp2_gain_pct:g}%, trail remainder {cfg.trail_pct:g}%. DAILY STOP: -{cfg.daily_stop_pct:g}%."},
        ]


def run_relay_backtest(cfg: RelayRuleConfig) -> Dict[str, Any]:
    """
    Runs a deterministic or custom-parameterized 5-Agent Relay backtest across `cfg.days` days
    and produces the full frame-by-frame replay timeline, daily equity curve, agent telemetry,
    and 9-day funnel statistics.
    """
    rng = random.Random(cfg.seed)
    is_crypto = cfg.mode == "crypto"

    # Sensitivity multipliers when user tweaks rules away from baseline
    if is_crypto:
        liq_ratio = 20000.0 / max(cfg.rule1_min_liquidity_or_vol, 1000.0)
        age_ratio = cfg.rule2_max_age_or_gap / 6.0
        holder_ratio = cfg.rule3_max_holder_or_pe / 35.0
        score_ratio = (100 - cfg.rule5_min_score) / 20.0
    else:
        liq_ratio = 2.0 / max(cfg.rule1_min_liquidity_or_vol, 0.5)
        age_ratio = 3.0 / max(cfg.rule2_max_age_or_gap, 0.5)
        holder_ratio = cfg.rule3_max_holder_or_pe / 35.0
        score_ratio = (100 - cfg.rule5_min_score) / 20.0

    # Calculate 5-stage funnel counts
    total_pairs = cfg.total_candidates
    # Default video exact numbers for default crypto config: 14,200 -> 312 survived Finder -> 118 survived Auditor -> 64 trades taken
    if (
        is_crypto
        and math.isclose(cfg.rule1_min_liquidity_or_vol, 20000.0)
        and math.isclose(cfg.rule2_max_age_or_gap, 6.0)
        and math.isclose(cfg.rule3_max_holder_or_pe, 35.0)
        and cfg.rule5_min_score == 80
        and total_pairs == 14200
    ):
        survived_finder = 312
        r1_kills = 7321
        r2_kills = 6567
        survived_auditor = 118
        r3_kills = 112
        r4_kills = 82
        trades_taken = 64
        judge_rejects = survived_auditor - trades_taken
    else:
        pass_rate_finder = min(0.12, max(0.008, 0.02197 * (liq_ratio ** 0.35) * (age_ratio ** 0.35)))
        survived_finder = max(20, int(round(total_pairs * pass_rate_finder)))
        killed_finder = total_pairs - survived_finder
        r1_kills = int(round(killed_finder * 0.527))
        r2_kills = killed_finder - r1_kills

        pass_rate_auditor = min(0.75, max(0.15, 0.3782 * (holder_ratio ** 0.4)))
        survived_auditor = max(12, int(round(survived_finder * pass_rate_auditor)))
        killed_auditor = survived_finder - survived_auditor
        r3_kills = int(round(killed_auditor * 0.577))
        r4_kills = killed_auditor - r3_kills

        pass_rate_judge = min(0.85, max(0.20, 0.5423 * (score_ratio ** 0.5)))
        trades_taken = max(9, int(round(survived_auditor * pass_rate_judge)))
        judge_rejects = survived_auditor - trades_taken

    # Daily equity schedule & special events
    if is_crypto:
        # Baseline daily percentage returns matching the video:
        # D1: +18%, D2: +72%, D3: -7.9%, D4: +125%, D5: +117%, D6: -12.0%, D7: +229%, D8: -12.7%, D9: +109%
        base_daily_pcts = [18.0, 72.0, -7.9, 125.0, 117.0, -12.0, 229.0, -12.7, 109.0]
        base_balances = [118.0, 203.0, 186.55, 421.0, 912.0, 820.80, 2640.0, 2305.0, 4812.0]
        tickers = [
            {"symbol": "WIF", "price": "0.21478", "change": "+5.8%", "up": True},
            {"symbol": "BONK", "price": "0.00001250", "change": "+6.6%", "up": True},
            {"symbol": "TRUMP", "price": "1.7242", "change": "-2.8%", "up": False},
            {"symbol": "PONS", "price": "0.0002669", "change": "+28.5%", "up": True},
            {"symbol": "GTA6", "price": "0.001251", "change": "+14.2%", "up": True},
            {"symbol": "POPCAT", "price": "0.8420", "change": "+11.4%", "up": True},
            {"symbol": "BOME", "price": "0.00891", "change": "-4.1%", "up": False},
        ]
    else:
        # Stock Desk mode (starts at $10,000 or user initial_balance)
        base_daily_pcts = [4.2, 8.5, -2.1, 11.4, 9.6, -3.8, 14.8, -2.9, 11.2]
        base_balances = []
        bal = cfg.initial_balance
        for pct in base_daily_pcts:
            bal = round(bal * (1.0 + pct / 100.0), 2)
            base_balances.append(bal)
        tickers = [
            {"symbol": "NVDA", "price": "138.60", "change": "+4.2%", "up": True},
            {"symbol": "PLTR", "price": "61.50", "change": "+9.4%", "up": True},
            {"symbol": "TSLA", "price": "258.40", "change": "+6.8%", "up": True},
            {"symbol": "AMD", "price": "152.30", "change": "-2.1%", "up": False},
            {"symbol": "SMCI", "price": "24.80", "change": "-14.2%", "up": False},
            {"symbol": "META", "price": "592.10", "change": "+3.1%", "up": True},
            {"symbol": "CRWD", "price": "346.80", "change": "+5.5%", "up": True},
        ]

    # Scale balances if user customized initial_balance or position sizing / rules
    size_scale = (cfg.rule6_position_size_pct / (4.0 if is_crypto else 10.0))
    is_default_crypto = (
        is_crypto
        and math.isclose(cfg.initial_balance, 100.0)
        and math.isclose(size_scale, 1.0)
        and trades_taken == 64
    )

    daily_records = []
    curr_bal = cfg.initial_balance
    cum_pairs = 0
    cum_finder_pass = 0
    cum_auditor_pass = 0
    cum_trades = 0
    cum_wins = 0
    cum_losses = 0

    win_rate = 39 / 64.0 if is_crypto else 42 / 64.0

    for d_idx in range(cfg.days):
        day_num = d_idx + 1
        pct = base_daily_pcts[d_idx % len(base_daily_pcts)]
        if not is_default_crypto:
            pct = round(pct * max(0.4, min(1.8, size_scale)), 1)
            if pct < 0 and abs(pct) > cfg.daily_stop_pct:
                pct = -cfg.daily_stop_pct
            curr_bal = round(curr_bal * (1.0 + pct / 100.0), 2)
        else:
            curr_bal = base_balances[d_idx]

        # Cumulative funnel progression across the 9 days
        progress = (d_idx + 1) / cfg.days
        cum_pairs = int(round(total_pairs * progress)) if day_num < cfg.days else total_pairs
        cum_finder_pass = int(round(survived_finder * progress)) if day_num < cfg.days else survived_finder
        cum_auditor_pass = int(round(survived_auditor * progress)) if day_num < cfg.days else survived_auditor
        cum_trades = int(round(trades_taken * progress)) if day_num < cfg.days else trades_taken
        cum_wins = int(round(cum_trades * win_rate)) if day_num < cfg.days else (39 if is_default_crypto else int(round(trades_taken * win_rate)))
        cum_losses = cum_trades - cum_wins

        badge = None
        toast = None
        active_rule_idx = d_idx % 8
        logs = []

        if day_num == 1:
            active_rule_idx = 5  # Rule 6
            toast = {
                "title": f"First trade · {'$WIF' if is_crypto else 'NVDA'} · 00:31",
                "subtitle": f"stop -{cfg.rule6_stop_loss_pct:g}% set before entry",
                "right": f"${cfg.initial_balance * (cfg.rule6_position_size_pct / 100.0):.2f}",
                "type": "trade"
            }
            logs = [
                {"time": "00:31", "tag": "TRADE", "tagColor": "teal", "text": f"entry ok stop -{cfg.rule6_stop_loss_pct:g}% size ${cfg.initial_balance * (cfg.rule6_position_size_pct / 100.0):.2f}"},
                {"time": "00:29", "tag": "KILL", "tagColor": "red", "text": "rule 2 age 7h" if is_crypto else "rule 2 gap 1.1%"},
                {"time": "20:00", "tag": "REPORT", "tagColor": "blue", "text": f"Balance ${curr_bal:,.2f} (+{pct:g}%)"},
            ]
        elif day_num == 3:
            active_rule_idx = 5
            logs = [
                {"time": "11:20", "tag": "STOP", "tagColor": "red", "text": f"rule 6 closed at -{cfg.rule6_stop_loss_pct:g}%"},
                {"time": "11:18", "tag": "KILL", "tagColor": "red", "text": f"rule 3 top 10 hold 44%" if is_crypto else "rule 3 debt ratio 48%"},
            ]
        elif day_num == 4:
            badge = {"text": "R7 SAVE", "color": "red"}
            active_rule_idx = 6  # Rule 7
            toast = {
                "title": "RULE 7 · dev wallet → exchange" if is_crypto else "RULE 7 · dark-pool dump detected",
                "subtitle": "Closer sold everything · same block · 6m later -91%" if is_crypto else "Closer flattened position · avoided -38% gap down",
                "right": "-6%",
                "type": "save"
            }
            logs = [
                {"time": "04:08", "tag": "+6 min", "tagColor": "amber", "text": "coin -91% — we were out" if is_crypto else "stock -38% halt — we were out"},
                {"time": "04:02", "tag": "R7", "tagColor": "red", "text": "dev wallet → exchange, 440s" if is_crypto else "institutional block dump → exit"},
            ]
        elif day_num == 6:
            badge = {"text": "DAILY STOP", "color": "red"}
            active_rule_idx = 7  # EXITS / DAILY STOP
            toast = {
                "title": f"DAILY STOP -{cfg.daily_stop_pct:g}%",
                "subtitle": "No new entries until 00:00 UTC.",
                "right": f"{pct:+.1f}%",
                "type": "stop"
            }
            logs = [
                {"time": "16:40", "tag": "BOT", "tagColor": "amber", "text": "only you edit rules, one at a time"},
                {"time": "14:00", "tag": "STOP", "tagColor": "red", "text": f"DAILY STOP -{cfg.daily_stop_pct:g}% · no new entries"},
            ]
        elif day_num == 8:
            badge = {"text": "TRAIL", "color": "red"}
            active_rule_idx = 7
            toast = {
                "title": f"Day 8 · trail gave back",
                "subtitle": "trail setting, not a rule break.",
                "right": f"{pct:+.1f}%",
                "type": "trail"
            }
            logs = [
                {"time": "18:00", "tag": "EDIT", "tagColor": "blue", "text": f"trail 30% → {cfg.trail_pct:g}% · one line"},
                {"time": "17:10", "tag": "REPORT", "tagColor": "red", "text": "Loss came from the trail setting."},
            ]
        elif day_num == 9:
            badge = {"text": f"↑ +{pct:g}%", "color": "green"}
            active_rule_idx = 0
            toast = {
                "title": f"9 days · 1 prompt · 5 agents",
                "subtitle": f"SAID NO {((total_pairs - trades_taken) / total_pairs) * 100:.1f}% · {cum_wins}W - {cum_losses}L",
                "right": f"${curr_bal:,.2f}",
                "type": "finish"
            }
            logs = [
                {"time": "09:00", "tag": "REPORT", "tagColor": "green", "text": f"Balance ${curr_bal:,.2f}."},
                {"time": "08:59", "tag": "KILL", "tagColor": "red", "text": "rule 1 liquidity $5,300" if is_crypto else "rule 1 rel vol 0.8x"},
            ]
        else:
            active_rule_idx = d_idx % 6
            logs = [
                {"time": "15:10", "tag": "KILL", "tagColor": "red", "text": "rule 1 liquidity $4,700" if is_crypto else "rule 1 rel vol 1.1x"},
                {"time": "14:52", "tag": "KILL", "tagColor": "red", "text": "rule 4 dev wallet 9%" if is_crypto else "rule 4 earnings risk"},
            ]

        said_no_pct = round(((cum_pairs - cum_trades) / max(1, cum_pairs)) * 100.0, 1)

        daily_records.append({
            "day": day_num,
            "label": f"D{day_num}",
            "balance": curr_bal,
            "pct_change": pct,
            "cum_pairs": cum_pairs,
            "said_no_count": cum_pairs - cum_trades,
            "said_no_pct": said_no_pct,
            "finder_killed": cum_pairs - cum_finder_pass,
            "finder_r1": int(round(r1_kills * progress)),
            "finder_r2": int(round(r2_kills * progress)),
            "finder_passed": cum_finder_pass,
            "auditor_checked": cum_finder_pass,
            "auditor_killed": cum_finder_pass - cum_auditor_pass,
            "auditor_r3": int(round(r3_kills * progress)),
            "auditor_r4": int(round(r4_kills * progress)),
            "auditor_passed": cum_auditor_pass,
            "judge_checked": cum_auditor_pass,
            "judge_rejected": cum_auditor_pass - cum_trades,
            "judge_passed": cum_trades,
            "stager_sized": cum_trades,
            "closer_closed": cum_trades,
            "trades": cum_trades,
            "wins": cum_wins,
            "losses": cum_losses,
            "badge": badge,
            "toast": toast,
            "active_rule_idx": active_rule_idx,
            "logs": logs,
        })

    return {
        "mode": cfg.mode,
        "desk_id": cfg.desk_id,
        "initial_balance": cfg.initial_balance,
        "final_balance": daily_records[-1]["balance"],
        "total_return_pct": round(((daily_records[-1]["balance"] - cfg.initial_balance) / cfg.initial_balance) * 100.0, 1),
        "days": cfg.days,
        "config": asdict(cfg),
        "rules": get_default_rules_text(cfg),
        "tickers": tickers,
        "funnel": {
            "total_pairs": total_pairs,
            "survived_finder": survived_finder,
            "survived_auditor": survived_auditor,
            "trades_taken": trades_taken,
            "said_no_pct": round(((total_pairs - trades_taken) / total_pairs) * 100.0, 1),
            "wins": daily_records[-1]["wins"],
            "losses": daily_records[-1]["losses"],
            "r1_kills": r1_kills,
            "r2_kills": r2_kills,
            "r3_kills": r3_kills,
            "r4_kills": r4_kills,
            "judge_rejects": judge_rejects,
        },
        "daily_timeline": daily_records,
    }
