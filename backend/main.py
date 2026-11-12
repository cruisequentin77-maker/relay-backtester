"""
FastAPI Backend Server for Relay Backtester (Grok Bot Style 5-Agent Replay Engine)
Provides REST API endpoints for running custom backtests, parsing natural language "One Prompt" rules,
and switching between Crypto (On-Chain Meme/Token) and Stock (Equity Technical/Fundamental) modes.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import RelayRuleConfig, run_relay_backtest
from market_data import catalog_payload, load_bars
from martingale import MartingaleConfig, load_chinext_bars, run_martingale
from simulate import bar_defaults_payload, config_from_fields, run_symbol_backtest

app = FastAPI(
    title="Relay Backtester API",
    description="Open-source 5-Agent Rule Pipeline Backtesting & Visual Replay Engine (Crypto & Stock)",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BacktestRequest(BaseModel):
    mode: str = "crypto"
    desk_id: Optional[str] = Field(default=None, max_length=40)
    initial_balance: Optional[float] = Field(default=None, ge=10, le=1_000_000_000)
    days: int = Field(default=9, ge=1, le=30)
    total_candidates: Optional[int] = Field(default=None, ge=10, le=200_000)
    rule1_min_liquidity_or_vol: Optional[float] = Field(default=None, gt=0, le=1e12)
    rule2_max_age_or_gap: Optional[float] = Field(default=None, gt=0, le=1e6)
    rule3_max_holder_or_pe: Optional[float] = Field(default=None, gt=0, le=1e6)
    rule5_min_score: Optional[int] = Field(default=None, ge=1, le=99)
    rule6_position_size_pct: Optional[float] = Field(default=None, ge=1, le=100)
    rule6_stop_loss_pct: Optional[float] = Field(default=None, ge=0.5, le=80)
    daily_stop_pct: Optional[float] = Field(default=None, ge=0.5, le=30)
    tp1_gain_pct: Optional[float] = Field(default=None, ge=0.5, le=500)
    tp2_gain_pct: Optional[float] = Field(default=None, ge=0.5, le=500)
    trail_pct: Optional[float] = Field(default=None, ge=0.5, le=80)
    prompt_text: Optional[str] = Field(default=None, max_length=2000)
    symbol: Optional[str] = Field(default=None, max_length=20)
    style: Optional[str] = Field(default=None, max_length=32)
    lookback: Optional[int] = Field(default=None, ge=40, le=1200)
    ma_fast: Optional[int] = Field(default=None, ge=2, le=120)
    ma_slow: Optional[int] = Field(default=None, ge=3, le=250)
    breakout_lookback: Optional[int] = Field(default=None, ge=5, le=120)
    min_breakout_pct: Optional[float] = Field(default=None, ge=0, le=20)
    pullback_pct: Optional[float] = Field(default=None, ge=0.2, le=20)
    max_atr_pct: Optional[float] = Field(default=None, ge=0.2, le=40)
    max_extension_pct: Optional[float] = Field(default=None, ge=0.5, le=80)
    min_rel_volume: Optional[float] = Field(default=None, ge=0.1, le=10)
    gap_pct: Optional[float] = Field(default=None, ge=1, le=30)
    fee_rate: Optional[float] = Field(default=None, ge=0, le=0.01)


def build_config_from_request(req: BacktestRequest) -> RelayRuleConfig:
    is_crypto = req.mode.lower() == "crypto"
    cfg = RelayRuleConfig(
        mode="crypto" if is_crypto else "stock",
        desk_id=req.desk_id or ("Desk 0001" if is_crypto else "Desk 0002"),
        initial_balance=req.initial_balance if req.initial_balance is not None else (100.0 if is_crypto else 10000.0),
        days=req.days,
        total_candidates=req.total_candidates if req.total_candidates is not None else (14200 if is_crypto else 4800),
        rule1_min_liquidity_or_vol=req.rule1_min_liquidity_or_vol if req.rule1_min_liquidity_or_vol is not None else (20000.0 if is_crypto else 2.0),
        rule2_max_age_or_gap=req.rule2_max_age_or_gap if req.rule2_max_age_or_gap is not None else (6.0 if is_crypto else 3.0),
        rule3_max_holder_or_pe=req.rule3_max_holder_or_pe if req.rule3_max_holder_or_pe is not None else 35.0,
        rule5_min_score=req.rule5_min_score if req.rule5_min_score is not None else 80,
        rule6_position_size_pct=req.rule6_position_size_pct if req.rule6_position_size_pct is not None else (4.0 if is_crypto else 10.0),
        rule6_stop_loss_pct=req.rule6_stop_loss_pct if req.rule6_stop_loss_pct is not None else (25.0 if is_crypto else 6.0),
        daily_stop_pct=req.daily_stop_pct if req.daily_stop_pct is not None else (10.0 if is_crypto else 4.0),
        tp1_gain_pct=req.tp1_gain_pct if req.tp1_gain_pct is not None else (100.0 if is_crypto else 20.0),
        tp2_gain_pct=req.tp2_gain_pct if req.tp2_gain_pct is not None else (200.0 if is_crypto else 40.0),
        trail_pct=req.trail_pct if req.trail_pct is not None else (25.0 if is_crypto else 8.0),
    )

    # Optional natural language "One Prompt" parser override
    if req.prompt_text:
        text = req.prompt_text
        m_liq = re.search(r"\$([\d,]+)", text)
        if m_liq and is_crypto:
            cfg.rule1_min_liquidity_or_vol = float(m_liq.group(1).replace(",", ""))
        m_age = re.search(r"(\d+(?:\.\d+)?)\s*hours", text, re.I)
        if m_age and is_crypto:
            cfg.rule2_max_age_or_gap = float(m_age.group(1))
        m_stop = re.search(r"stop\s+at\s*-?(\d+(?:\.\d+)?)%", text, re.I)
        if m_stop:
            cfg.rule6_stop_loss_pct = float(m_stop.group(1))
        m_trail = re.search(r"trail\s+(?:the\s+rest\s+)?(\d+(?:\.\d+)?)%", text, re.I)
        if m_trail:
            cfg.trail_pct = float(m_trail.group(1))

    return cfg


def _run_bar(req: BacktestRequest, mode: str) -> dict:
    symbol = (req.symbol or "").strip()
    if not symbol:
        symbol = {"index": "000300", "stock": "600519", "crypto": "BTCUSDT"}[mode]
    try:
        cfg = config_from_fields(mode, symbol, req.model_dump())
        remote_flag = os.environ.get("RELAY_ALLOW_REMOTE", "1").strip().lower()
        allow_remote = remote_flag in {"1", "true", "yes", "on"}
        bars, meta = load_bars(symbol, mode_hint=mode, allow_remote=allow_remote)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg.symbol = str(meta["symbol"])
    result = run_symbol_backtest(bars, cfg)
    result["symbol"] = cfg.symbol
    result["name"] = meta["name"]
    result["currency"] = meta["currency"]
    result["data"] = {
        "source": meta["source"],
        "vendor": meta["vendor"],
        "provenance": meta["provenance"],
        "historical": meta["historical"],
        "note": meta["note"],
        "adjust": meta["adjust"],
        "warning": meta["warning"],
        "requested_symbol": meta.get("requested_symbol") or symbol,
        "rows": len(result["events"]),
        "start": result["events"][0]["date"],
        "end": result["events"][-1]["date"],
    }
    return result


def _frontend_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend"


@app.get("/api/health")
def health_check():
    frontend = _frontend_dir()
    return {
        "status": "ok",
        "engine": "relay-backtester-v1",
        "modes": ["crypto", "index", "stock"],
        "static": frontend.is_dir(),
    }


@app.get("/api/catalog")
def get_catalog():
    return {
        "modes": ["crypto", "index", "stock"],
        "catalog": catalog_payload(),
        "bar_defaults": bar_defaults_payload(),
    }


@app.get("/api/presets")
def get_presets():
    crypto_cfg = build_config_from_request(BacktestRequest(mode="crypto"))
    stock_cfg = build_config_from_request(BacktestRequest(mode="stock"))
    crypto = run_relay_backtest(crypto_cfg)
    stock = run_relay_backtest(stock_cfg)
    crypto["engine"] = "relay"
    stock["engine"] = "relay"
    return {
        "crypto": crypto,
        "stock": stock,
        "modes": ["crypto", "index", "stock"],
        "catalog": catalog_payload(),
        "bar_defaults": bar_defaults_payload(),
    }


@app.post("/api/backtest")
def execute_backtest(req: BacktestRequest):
    mode = (req.mode or "crypto").strip().lower()
    if mode not in {"crypto", "stock", "index"}:
        raise HTTPException(status_code=400, detail="mode 只能是 crypto、index 或 stock")
    symbol = (req.symbol or "").strip()
    if mode == "index" or symbol:
        return _run_bar(req, mode)
    cfg = build_config_from_request(req)
    result = run_relay_backtest(cfg)
    result["engine"] = "relay"
    return result


class MartingaleRequest(BaseModel):
    initial_cash: float = Field(default=1_000_000.0, ge=1_000, le=1_000_000_000)
    max_layers: int = Field(default=6, ge=1, le=8)
    drop_step: float = Field(default=0.05, gt=0, le=0.30)
    take_profit: float = Field(default=0.05, gt=0, le=0.50)
    fee_rate: float = Field(default=0.0003, ge=0, le=0.01)


@app.post("/api/martingale")
def execute_martingale(req: MartingaleRequest):
    """Long-only martingale on 创业板指 399006 daily closes."""
    cfg = MartingaleConfig(
        initial_cash=req.initial_cash,
        max_layers=req.max_layers,
        drop_step=req.drop_step,
        take_profit=req.take_profit,
        fee_rate=req.fee_rate,
    )
    try:
        cfg.validate()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run_martingale(load_chinext_bars(), cfg)


_frontend = _frontend_dir()
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
