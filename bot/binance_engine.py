"""
🏛️ Binance Quantitative Smart Money Engine
Provides real-time institutional metrics directly from Binance Spot & Futures APIs:
- Ultra-low latency OHLCV klines with Taker Buy/Sell volume delta
- Futures Open Interest (OI) capital flow validation
- Funding Rate & liquidation squeeze analysis
- Top Trader (Whale) Long/Short account ratio
"""

import os
from dataclasses import dataclass
import html
import requests
import pandas as pd
from bot.symbols import BINANCE_SYMBOLS


BINANCE_SPOT_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

BINANCE_FUTURES_HOST = "https://fapi.binance.com"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"})


@dataclass(frozen=True)
class BinanceSmartMoneyMetrics:
    symbol: str
    taker_buy_pct: float | None = None
    taker_sell_pct: float | None = None
    open_interest: float | None = None
    funding_rate_pct: float | None = None
    funding_bias: str = "Neutral"
    top_trader_long_pct: float | None = None
    top_trader_ls_ratio: float | None = None
    confidence_modifier: int = 0
    bias: str = "NEUTRAL"
    summary_text: str = ""

    def is_vetoed(self, action: str) -> tuple[bool, str]:
        """
        Active Institutional VETO:
        Returns (True, reason) if institutional positioning (whales, funding rate, taker delta)
        directly opposes the intended trade direction, meaning retail is being trapped.
        """
        if action == "BUY":
            if self.top_trader_long_pct is not None and self.top_trader_long_pct <= 42.0:
                short_pct = 100.0 - self.top_trader_long_pct
                return True, f"VETO: Top Trader Whales are heavily short ({short_pct:.1f}% short, L/S: {self.top_trader_ls_ratio:.2f})"
            if self.funding_rate_pct is not None and self.funding_rate_pct >= 0.035:
                return True, f"VETO: Funding rate overheated ({self.funding_rate_pct:+.4f}%), severe long liquidation cascade risk"
            if self.taker_buy_pct is not None and self.taker_buy_pct <= 38.0:
                return True, f"VETO: Heavy aggressive taker selling ({100.0 - self.taker_buy_pct:.1f}% sells)"

        elif action == "SELL":
            if self.top_trader_long_pct is not None and self.top_trader_long_pct >= 58.0:
                return True, f"VETO: Top Trader Whales are heavily long ({self.top_trader_long_pct:.1f}% long, L/S: {self.top_trader_ls_ratio:.2f})"
            if self.funding_rate_pct is not None and self.funding_rate_pct <= -0.020:
                return True, f"VETO: Funding rate deeply negative ({self.funding_rate_pct:+.4f}%), high risk of short squeeze"
            if self.taker_buy_pct is not None and self.taker_buy_pct >= 62.0:
                return True, f"VETO: Heavy aggressive taker buying ({self.taker_buy_pct:.1f}% buys)"

        return False, ""


def get_binance_symbol_pair(symbol: str) -> tuple[str | None, str | None]:
    """
    Returns (spot_symbol, futures_symbol) for a given internal symbol.
    """
    mapping = BINANCE_SYMBOLS.get(symbol)
    if mapping:
        return mapping.get("spot"), mapping.get("futures")

    # Clean generic fallback
    clean = symbol.replace("_USD", "USDT").replace("_", "").upper()
    return clean, clean


def fetch_binance_klines(
    symbol: str,
    interval: str = "15m",
    limit: int = 100,
    timeout: float = 2.0,
) -> pd.DataFrame:
    """
    Fetches high-resolution OHLCV candles with Taker Buy Volume from Binance.
    Tries multiple Binance endpoints for high availability.
    """
    spot_sym, fut_sym = get_binance_symbol_pair(symbol)
    target_sym = spot_sym or fut_sym or symbol

    for host in ["https://data-api.binance.vision", "https://api.binance.com"]:
        try:
            url = f"{host}/api/v3/klines"
            res = _SESSION.get(
                url,
                params={"symbol": target_sym, "interval": interval, "limit": limit},
                timeout=timeout,
            )
            if res.status_code == 200:
                raw_data = res.json()
                if raw_data and isinstance(raw_data, list):
                    rows = []
                    for item in raw_data:
                        rows.append({
                            "time": pd.to_datetime(item[0], unit="ms", utc=True),
                            "open": float(item[1]),
                            "high": float(item[2]),
                            "low": float(item[3]),
                            "close": float(item[4]),
                            "volume": float(item[5]),
                            "taker_buy_base_vol": float(item[9]),
                            "taker_buy_quote_vol": float(item[10]),
                        })
                    df = pd.DataFrame(rows)
                    return df.dropna().sort_values("time").reset_index(drop=True)
            elif res.status_code == 400:
                break
        except Exception:
            continue

    # Try Futures endpoint if Spot failed or pair is Futures-only
    if fut_sym:
        try:
            url = f"{BINANCE_FUTURES_HOST}/fapi/v1/klines"
            res = _SESSION.get(
                url,
                params={"symbol": fut_sym, "interval": interval, "limit": limit},
                timeout=timeout,
            )
            if res.status_code == 200:
                raw_data = res.json()
                if raw_data and isinstance(raw_data, list):
                    rows = []
                    for item in raw_data:
                        rows.append({
                            "time": pd.to_datetime(item[0], unit="ms", utc=True),
                            "open": float(item[1]),
                            "high": float(item[2]),
                            "low": float(item[3]),
                            "close": float(item[4]),
                            "volume": float(item[5]),
                            "taker_buy_base_vol": float(item[9]),
                            "taker_buy_quote_vol": float(item[10]),
                        })
                    df = pd.DataFrame(rows)
                    return df.dropna().sort_values("time").reset_index(drop=True)
        except Exception:
            pass

    raise RuntimeError(f"Could not fetch Binance klines for {symbol} ({target_sym})")


def fetch_binance_derivatives(symbol: str, timeout: float = 1.5) -> dict:
    """
    Fetches Open Interest, Funding Rate, and Top Trader Long/Short account ratio.
    """
    _, fut_sym = get_binance_symbol_pair(symbol)
    if not fut_sym:
        return {}

    # Skip unmocked live network calls in automated test suite or backtests
    if (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("BACKTEST_MODE")) and not (hasattr(_SESSION.get, "mock_calls") or hasattr(requests.get, "mock_calls")):
        return {}

    data: dict = {}

    # 1. Open Interest
    try:
        url_oi = f"{BINANCE_FUTURES_HOST}/fapi/v1/openInterest"
        res_oi = _SESSION.get(url_oi, params={"symbol": fut_sym}, timeout=timeout)
        if res_oi.status_code == 200:
            data["open_interest"] = float(res_oi.json().get("openInterest", 0))
    except Exception:
        pass

    # 2. Funding Rate
    try:
        url_fr = f"{BINANCE_FUTURES_HOST}/fapi/v1/fundingRate"
        res_fr = _SESSION.get(url_fr, params={"symbol": fut_sym, "limit": 1}, timeout=timeout)
        if res_fr.status_code == 200 and res_fr.json():
            fr_val = float(res_fr.json()[0].get("fundingRate", 0))
            data["funding_rate_pct"] = fr_val * 100
    except Exception:
        pass

    # 3. Top Trader Long/Short Account Ratio
    try:
        url_ls = f"{BINANCE_FUTURES_HOST}/futures/data/topLongShortAccountRatio"
        res_ls = _SESSION.get(url_ls, params={"symbol": fut_sym, "period": "15m", "limit": 1}, timeout=timeout)
        if res_ls.status_code == 200 and res_ls.json():
            item = res_ls.json()[0]
            data["top_trader_long_pct"] = float(item.get("longAccount", 0.5)) * 100
            data["top_trader_ls_ratio"] = float(item.get("longShortRatio", 1.0))
    except Exception:
        pass

    return data


def analyze_binance_smart_money(
    symbol: str,
    prices_df: pd.DataFrame | None = None,
) -> BinanceSmartMoneyMetrics:
    """
    Aggregates volume delta, Open Interest, Funding Rates, and Top Trader ratios
    into institutional confluence metrics.
    """
    # 1. Taker Buy/Sell Delta from Klines
    taker_buy_pct = None
    taker_sell_pct = None
    if prices_df is not None and "taker_buy_base_vol" in prices_df.columns and "volume" in prices_df.columns:
        recent_df = prices_df.iloc[-5:]
        total_vol = recent_df["volume"].sum()
        taker_buy_vol = recent_df["taker_buy_base_vol"].sum()
        if total_vol > 0:
            taker_buy_pct = (taker_buy_vol / total_vol) * 100
            taker_sell_pct = 100.0 - taker_buy_pct

    # 2. Derivatives metrics
    deriv = fetch_binance_derivatives(symbol)
    open_interest = deriv.get("open_interest")
    funding_rate_pct = deriv.get("funding_rate_pct")
    top_trader_long_pct = deriv.get("top_trader_long_pct")
    top_trader_ls_ratio = deriv.get("top_trader_ls_ratio")

    # 3. Evaluate Smart Money Confluence & Confidence Modifier
    modifier = 0
    bull_signals = 0
    bear_signals = 0

    # Taker volume bias
    if taker_buy_pct is not None:
        if taker_buy_pct >= 58.0:
            bull_signals += 1
            modifier += 5
        elif taker_buy_pct <= 42.0:
            bear_signals += 1
            modifier += 5

    # Whale Top Trader positioning
    if top_trader_long_pct is not None:
        if top_trader_long_pct >= 58.0:
            bull_signals += 1
            modifier += 5
        elif top_trader_long_pct <= 42.0:
            bear_signals += 1
            modifier += 5

    # Funding rate bias
    funding_bias = "Healthy (Neutral)"
    if funding_rate_pct is not None:
        if funding_rate_pct >= 0.025:
            funding_bias = "⚠️ Overheated Longs (Flush Risk)"
            bear_signals += 1
        elif funding_rate_pct <= -0.012:
            funding_bias = "🔥 Short Squeeze Opportunity"
            bull_signals += 1
            modifier += 5

    bias = "BULLISH" if bull_signals > bear_signals else ("BEARISH" if bear_signals > bull_signals else "NEUTRAL")

    # Build concise Telegram summary lines
    lines = []
    if taker_buy_pct is not None:
        emoji = "🟢" if taker_buy_pct >= 50 else "🔴"
        lines.append(f"• Taker Volume Delta: {emoji} <code>{taker_buy_pct:.1f}% Buys</code> / <code>{taker_sell_pct:.1f}% Sells</code>")

    if top_trader_long_pct is not None and top_trader_ls_ratio is not None:
        emoji = "🟢" if top_trader_long_pct >= 50 else "🔴"
        lines.append(f"• Top Trader Whales: {emoji} <code>{top_trader_long_pct:.1f}% Long</code> (L/S: <code>{top_trader_ls_ratio:.2f}</code>)")

    if funding_rate_pct is not None:
        lines.append(f"• Funding Rate: <code>{funding_rate_pct:+.4f}%</code> ({html.escape(funding_bias)})")

    if open_interest is not None and open_interest > 0:
        lines.append(f"• Futures Open Interest: <code>{open_interest:,.0f} Contracts</code>")

    summary_text = "\n".join(lines)

    return BinanceSmartMoneyMetrics(
        symbol=symbol,
        taker_buy_pct=taker_buy_pct,
        taker_sell_pct=taker_sell_pct,
        open_interest=open_interest,
        funding_rate_pct=funding_rate_pct,
        funding_bias=funding_bias,
        top_trader_long_pct=top_trader_long_pct,
        top_trader_ls_ratio=top_trader_ls_ratio,
        confidence_modifier=min(modifier, 15),
        bias=bias,
        summary_text=summary_text,
    )
