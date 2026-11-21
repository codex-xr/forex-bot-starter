import os
from pathlib import Path
import time

import pandas as pd
import requests
from dotenv import load_dotenv

from bot.data import load_price_data
from bot.symbols import (
    BINANCE_SYMBOLS,
    DEX_POOLS,
    DEX_POOL_FALLBACKS,
    GATE_SYMBOLS,
    SYMBOL_ALIASES,
    normalize_symbol,
)


def _map_interval_to_binance(interval: str) -> str:
    s = interval.lower().strip()
    if "15" in s:
        return "15m"
    if "5" in s:
        return "5m"
    if "1" in s and ("h" in s or "hour" in s):
        return "1h"
    if "4" in s and ("h" in s or "hour" in s):
        return "4h"
    if "d" in s or "day" in s:
        return "1d"
    return "15m"


def _map_interval_to_dex(interval: str) -> tuple[str, int]:
    s = interval.lower().strip()
    if "4" in s and ("h" in s or "hour" in s):
        return "hour", 4
    if "1" in s and ("h" in s or "hour" in s):
        return "hour", 1
    if "d" in s or "day" in s:
        return "day", 1
    if "5" in s:
        return "minute", 5
    return "minute", 15


def fetch_dex_candles(
    network: str,
    pool_address: str,
    aggregate: int = 15,
    limit: int = 250,
    timeframe: str = "minute",
) -> pd.DataFrame:
    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}"
    response = requests.get(
        url,
        params={"aggregate": aggregate, "limit": min(limit, 1000)},
        headers={"Accept": "application/json"},
        timeout=4.0,
    )
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code} from GeckoTerminal for pool {pool_address}")

    payload = response.json()
    raw_list = payload.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    if not raw_list:
        raise RuntimeError(f"No candle data available from DEX for pool {pool_address}")

    rows = []
    for item in raw_list:
        rows.append({
            "time": pd.to_datetime(item[0], unit="s", utc=True),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]) if len(item) > 5 else 0.0,
        })

    data = pd.DataFrame(rows)
    return data.dropna().sort_values("time").reset_index(drop=True)


def fetch_gate_candles(gate_pair: str, interval: str = "15min", limit: int = 250) -> pd.DataFrame:
    """
    Fetches OHLCV candlesticks from Gate.io public Spot REST API.
    Gate format per bar: [timestamp_s, quote_vol, close, high, low, open, base_vol, is_closed].
    """
    g_interval = _map_interval_to_binance(interval)
    url = "https://api.gateio.ws/api/v4/spot/candlesticks"
    response = None
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                params={"currency_pair": gate_pair, "interval": g_interval, "limit": min(limit, 1000)},
                headers={"Accept": "application/json"},
                timeout=6.0,
            )
            if response.ok:
                break
        except requests.exceptions.Timeout:
            if attempt == 0:
                time.sleep(0.25)
                continue
            raise
    if response is None or not response.ok:
        status = response.status_code if response is not None else "no-response"
        raise RuntimeError(f"HTTP {status} from Gate.io for {gate_pair}")

    raw_list = response.json()
    if not raw_list or not isinstance(raw_list, list):
        raise RuntimeError(f"No candle data from Gate.io for {gate_pair}")

    rows = []
    for item in raw_list:
        if len(item) < 6:
            continue
        rows.append({
            "time": pd.to_datetime(int(item[0]), unit="s", utc=True),
            "open": float(item[5]),
            "high": float(item[3]),
            "low": float(item[4]),
            "close": float(item[2]),
            "volume": float(item[6]) if len(item) > 6 else 0.0,
        })

    data = pd.DataFrame(rows)
    return data.dropna().sort_values("time").reset_index(drop=True)


def fetch_live_candles(symbol: str, interval: str = "15min", outputsize: int = 250) -> pd.DataFrame:
    symbol = normalize_symbol(symbol)
    tf, agg = _map_interval_to_dex(interval)

    # 1. Specialized DEX Pools (e.g. $ANSEM)
    if symbol in DEX_POOLS:
        network, pool = DEX_POOLS[symbol]
        try:
            return fetch_dex_candles(network, pool, aggregate=agg, limit=outputsize, timeframe=tf)
        except Exception as dex_err:
            fallback = Path("data") / f"{symbol.lower()}.csv"
            if fallback.exists():
                return load_price_data(fallback)
            raise RuntimeError(f"DEX fetch failed: {dex_err}")

    # 2. Binance Quantitative High-Speed Engine for all Crypto & Memecoins
    if symbol in BINANCE_SYMBOLS:
        try:
            from bot.binance_engine import fetch_binance_klines
            b_interval = _map_interval_to_binance(interval)
            b_df = fetch_binance_klines(symbol, interval=b_interval, limit=outputsize)
            if b_df is not None and len(b_df) >= 10:
                return b_df
        except Exception as b_err:
            print(f"[MarketData] Binance fetch fallback for {symbol}: {b_err}")

    # 3. Gate.io Public Spot CEX Fallback (handles Futures-only / geo-restricted pairs cleanly)
    if symbol in GATE_SYMBOLS:
        try:
            g_df = fetch_gate_candles(GATE_SYMBOLS[symbol], interval=interval, limit=outputsize)
            if g_df is not None and len(g_df) >= 10:
                return g_df
        except Exception as g_err:
            print(f"[MarketData] Gate fallback failed for {symbol}: {g_err}")

    # 4. GeckoTerminal DEX Pool Fallback (e.g. BRETT, MOG, ELON)
    if symbol in DEX_POOL_FALLBACKS:
        network, pool = DEX_POOL_FALLBACKS[symbol]
        try:
            dex_df = fetch_dex_candles(network, pool, aggregate=agg, limit=outputsize, timeframe=tf)
            if dex_df is not None and len(dex_df) >= 10:
                return dex_df
        except Exception as dex_err:
            print(f"[MarketData] DEX fallback failed for {symbol}: {dex_err}")

    # 5. Local CSV Fallback (for backtests / offline tests)
    fallback = Path("data") / f"{symbol.lower()}.csv"
    if fallback.exists():
        return load_price_data(fallback)

    raise RuntimeError(f"Real-time data temporarily unavailable for {symbol}")

