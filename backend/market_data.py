"""Bundled OHLCV plus a public-source loader that falls back to a sample path.

The demo never requires a paid feed. Catalog symbols ship as CSV under
``backend/data``. If a file is missing, the loader tries the public source
recorded on the symbol and, when that fails, synthesizes a deterministic
sample so the index / stock / crypto paths still run offline.
"""
from __future__ import annotations

import csv
import json
import random
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"

Fetcher = Callable[["Spec", Path], None]


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Spec:
    symbol: str
    name: str
    mode: str
    currency: str
    file: str
    vendor: str
    secid: str = ""
    sina: str = ""
    adjust: str = "none"
    note: str = ""


CATALOG: Tuple[Spec, ...] = (
    Spec("000300", "沪深300", "index", "CNY", "000300.csv", "eastmoney", "1.000300", adjust="none", note="东方财富指数日线"),
    Spec("000905", "中证500", "index", "CNY", "000905.csv", "eastmoney", "1.000905", adjust="none", note="东方财富指数日线"),
    Spec("399006", "创业板指", "index", "CNY", "399006.csv", "eastmoney", "0.399006", adjust="none", note="东方财富指数日线，沿用原创业板数据包"),
    Spec("000016", "上证50", "index", "CNY", "000016.csv", "eastmoney", "1.000016", adjust="none", note="东方财富指数日线"),
    Spec("NDX", "纳斯达克100", "index", "USD", "NDX.csv", "eastmoney", "100.NDX", adjust="none", note="东方财富指数日线"),
    Spec("SPX", "标普500", "index", "USD", "SPX.csv", "eastmoney", "100.SPX", adjust="none", note="东方财富指数日线"),
    Spec("600519", "贵州茅台", "stock", "CNY", "600519.csv", "sina", "1.600519", "sh600519", note="新浪日线，未复权"),
    Spec("000858", "五粮液", "stock", "CNY", "000858.csv", "sina", "0.000858", "sz000858", note="新浪日线，未复权"),
    Spec(
        "300750",
        "宁德时代",
        "stock",
        "CNY",
        "300750.csv",
        "sina",
        "0.300750",
        "sz300750",
        adjust="2023-04-26 每10股转增8股并派息2.52元，已前复权",
        note="新浪日线；2023-04-26 权益分派已按转增与派息前复权",
    ),
    Spec("601318", "中国平安", "stock", "CNY", "601318.csv", "sina", "1.601318", "sh601318", note="新浪日线，未复权"),
    Spec("BTCUSDT", "比特币", "crypto", "USDT", "BTCUSDT.csv", "binance", note="Binance 现货日线"),
    Spec("ETHUSDT", "以太坊", "crypto", "USDT", "ETHUSDT.csv", "binance", note="Binance 现货日线"),
)

# 399300 is the Shenzhen listing of the same CSI 300 series as 000300.
ALIASES = {"399300": "000300"}

_BY_SYMBOL = {spec.symbol: spec for spec in CATALOG}


def normalize_symbol(symbol: str) -> str:
    text = (symbol or "").strip().upper()
    if not text:
        raise ValueError("标的代码为空")
    return ALIASES.get(text, text)


def resolve_spec(symbol: str) -> Optional[Spec]:
    return _BY_SYMBOL.get(normalize_symbol(symbol))


def catalog_payload() -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = {"crypto": [], "index": [], "stock": []}
    for spec in CATALOG:
        item = {
            "symbol": spec.symbol,
            "name": spec.name,
            "mode": spec.mode,
            "currency": spec.currency,
            "vendor": spec.vendor,
            "note": spec.note,
            "adjust": spec.adjust,
        }
        if spec.symbol == "000300":
            item["aliases"] = "399300"
        grouped[spec.mode].append(item)
    return grouped


def _request(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _write_rows(path: Path, rows: Sequence[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "amount"])
        writer.writerows(rows)


def _download_eastmoney(secid: str, path: Path, fqt: str = "0") -> None:
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt={fqt}&beg=20180101&end=20500101&lmt=10000"
    )
    payload = json.loads(
        _request(url, {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}).decode("utf-8")
    )
    klines = (payload.get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError(f"东方财富未返回 {secid} 的K线")
    rows = []
    for row in klines:
        parts = row.split(",")
        rows.append([parts[0], parts[1], parts[3], parts[4], parts[2], parts[5], parts[6]])
    _write_rows(path, rows)


def _download_sina(sina_symbol: str, path: Path) -> None:
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={sina_symbol}&scale=240&ma=no&datalen=2000"
    )
    payload = json.loads(_request(url).decode("utf-8"))
    if not payload:
        raise RuntimeError(f"新浪未返回 {sina_symbol} 的K线")
    rows = []
    for row in payload:
        close = float(row["close"])
        volume = float(row["volume"])
        rows.append([row["day"], row["open"], row["high"], row["low"], row["close"], row["volume"], f"{close * volume:.2f}"])
    _write_rows(path, rows)


def _download_binance(symbol: str, path: Path) -> None:
    start = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows_raw = []
    while start < end:
        url = (
            "https://data-api.binance.vision/api/v3/klines"
            f"?symbol={symbol}&interval=1d&startTime={start}&endTime={end}&limit=1000"
        )
        batch = json.loads(_request(url).decode("utf-8"))
        if not isinstance(batch, list) or not batch:
            break
        rows_raw.extend(batch)
        nxt = int(batch[-1][0]) + 1
        if nxt <= start or len(batch) < 1000:
            break
        start = nxt
    if not rows_raw:
        raise RuntimeError(f"Binance 未返回 {symbol} 的K线")
    seen = {int(item[0]): item for item in rows_raw}
    ordered = [seen[key] for key in sorted(seen)]
    rows = []
    for item in ordered:
        day = datetime.fromtimestamp(int(item[0]) / 1000, timezone.utc).strftime("%Y-%m-%d")
        rows.append([day, item[1], item[2], item[3], item[4], item[5], item[7]])
    _write_rows(path, rows)


def download_spec(spec: Spec, path: Path) -> None:
    """Fetch one symbol. Raises on failure; callers decide whether to fall back."""
    if spec.vendor == "binance" or spec.mode == "crypto":
        _download_binance(spec.symbol, path)
        return
    fqt = "1" if spec.mode == "stock" else "0"
    if spec.secid:
        try:
            _download_eastmoney(spec.secid, path, fqt=fqt)
            return
        except Exception:
            if not spec.sina:
                raise
    if spec.sina:
        _download_sina(spec.sina, path)
        return
    raise RuntimeError(f"没有可用的公共行情源：{spec.symbol}")


def synthesize_bars(spec: Spec, count: int = 320) -> List[Bar]:
    """Deterministic demonstration path. Dates and prices are intentionally not a market history."""
    seed = zlib.crc32(spec.symbol.encode("utf-8")) & 0xFFFFFFFF
    rng = random.Random(seed)
    price = 100.0
    bars: List[Bar] = []
    for index in range(count):
        drift = 0.0016 if (index % 90) < 60 else -0.0045
        shock = rng.gauss(drift, 0.011)
        opened = price
        closed = max(1.0, opened * (1.0 + shock))
        wick = abs(rng.gauss(0, 0.004))
        high = max(opened, closed) * (1.0 + wick)
        low = min(opened, closed) * (1.0 - wick)
        volume = 1000.0 + abs(rng.gauss(0, 50))
        bars.append(Bar(f"SYNTH-{index + 1:04d}", opened, high, low, closed, volume))
        price = closed
    return bars


def read_bars(path: Path) -> List[Bar]:
    if not path.exists():
        raise ValueError(f"找不到行情文件 {path.name}")
    chosen: Dict[str, Bar] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                day = str(row["date"])[:10]
                opened = float(row["open"])
                high = float(row["high"])
                low = float(row["low"])
                closed = float(row["close"])
                volume = float(row.get("volume") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if min(opened, high, low, closed) <= 0:
                continue
            high = max(high, opened, closed)
            low = min(low, opened, closed)
            chosen[day] = Bar(day, opened, high, low, closed, max(0.0, volume))
    bars = [chosen[key] for key in sorted(chosen)]
    if len(bars) < 30:
        raise ValueError(f"{path.name} 里的有效K线不足")
    return bars


def _meta(spec: Spec, source: str, rows: int, warning: str = "") -> Dict[str, object]:
    if source == "sample":
        provenance = "synthetic_demonstration"
        historical = False
        name = f"演示序列（不是{spec.name}）"
        vendor = "synthetic"
        note = (
            "合成演示数据，不是历史行情。"
            "日期是 SYNTH-0001 这种序号，价格从 100 附近生成，"
            f"不能当作{spec.name}（{spec.symbol}）的真实走势。"
        )
    elif source == "remote":
        provenance = "remote_public_daily"
        historical = True
        name = spec.name
        vendor = spec.vendor
        note = f"{spec.note}。这是临时拉取的公开日线，不是实时行情，也不是合成数据。"
    else:
        provenance = "bundled_public_daily"
        historical = True
        name = spec.name
        vendor = spec.vendor
        note = f"{spec.note}。这是仓库里打包的公开日线，不是实时行情，也不是合成数据。"
    return {
        "symbol": spec.symbol,
        "name": name,
        "mode": spec.mode,
        "currency": spec.currency,
        "vendor": vendor,
        "source": source,
        "provenance": provenance,
        "historical": historical,
        "adjust": spec.adjust if historical else "not_applicable",
        "note": note,
        "rows": rows,
        "warning": warning,
    }


def _adhoc_spec(symbol: str, mode: str) -> Spec:
    if mode == "crypto":
        if not symbol.endswith("USDT"):
            raise ValueError("加密资产请使用币安现货代码，例如 BTCUSDT")
        return Spec(symbol, symbol, "crypto", "USDT", f"cache/{symbol}.csv", "binance", note="临时拉取的 Binance 日线")
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError("A股与宽基指数请使用 6 位代码，或从列表中选择")
    if mode == "index":
        secid = ("0." if symbol.startswith("399") else "1.") + symbol
        return Spec(symbol, symbol, "index", "CNY", f"cache/{symbol}.csv", "eastmoney", secid, note="临时拉取的指数日线")
    if mode == "stock":
        secid = ("1." if symbol.startswith(("5", "6", "9")) else "0.") + symbol
        prefix = "sh" if secid.startswith("1.") else "sz"
        return Spec(
            symbol,
            symbol,
            "stock",
            "CNY",
            f"cache/{symbol}.csv",
            "sina",
            secid,
            sina=prefix + symbol,
            note="临时拉取的个股日线",
        )
    raise ValueError("mode 只能是 crypto、index 或 stock")


def load_bars(
    symbol: str,
    mode_hint: Optional[str] = None,
    *,
    directory: Path = DATA_DIR,
    allow_remote: bool = True,
    fetcher: Optional[Fetcher] = None,
) -> Tuple[List[Bar], Dict[str, object]]:
    """Load bars for a catalog symbol or, when hinted, an ad-hoc code.

    Catalog files are preferred. A failed download never raises for a known
    symbol: the caller receives synthetic sample bars and ``source=sample``.
    """
    raw = (symbol or "").strip().upper()
    if not raw:
        raise ValueError("标的代码为空")
    canonical = ALIASES.get(raw, raw)
    spec = _BY_SYMBOL.get(canonical)
    if spec is None:
        if mode_hint not in {"crypto", "index", "stock"}:
            raise ValueError(f"目录中没有 {raw}。请从列表选择，或指定资产类别后再试。")
        spec = _adhoc_spec(canonical, mode_hint)
        path = directory / spec.file
        if path.exists():
            bars = read_bars(path)
            meta = _meta(spec, "remote", len(bars))
            meta["requested_symbol"] = raw
            return bars, meta
        if not allow_remote:
            raise ValueError(f"没有 {raw} 的本地样本，且当前禁止访问公共行情")
        try:
            (fetcher or download_spec)(spec, path)
        except Exception as exc:
            raise ValueError(f"拉取 {raw} 失败，也没有可回退的本地样本：{exc}") from exc
        bars = read_bars(path)
        meta = _meta(spec, "remote", len(bars))
        meta["requested_symbol"] = raw
        return bars, meta

    if mode_hint and mode_hint != spec.mode:
        label = {"crypto": "虚拟货币", "index": "宽基指数", "stock": "个股"}[spec.mode]
        raise ValueError(f"{spec.symbol}（{spec.name}）属于{label}，与当前资产类别不一致")

    path = directory / spec.file
    if path.exists():
        bars = read_bars(path)
        meta = _meta(spec, "bundled", len(bars))
        meta["requested_symbol"] = raw
        return bars, meta

    warning = ""
    if allow_remote:
        try:
            (fetcher or download_spec)(spec, path)
        except Exception as exc:
            warning = str(exc)
    if path.exists():
        bars = read_bars(path)
        meta = _meta(spec, "remote", len(bars), warning)
        meta["requested_symbol"] = raw
        return bars, meta

    bars = synthesize_bars(spec)
    meta = _meta(spec, "sample", len(bars), warning or "本地文件不存在")
    meta["requested_symbol"] = raw
    return bars, meta
