import os
from pathlib import Path
import time

import pandas as pd
import requests
from dotenv import load_dotenv

from bot.data import load_price_data
from bot.symbols import DEX_POOLS, SYMBOL_ALIASES, BINANCE_SYMBOLS


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


def fetch_dex_candles(network: str, pool_address: str, aggregate: int = 15, limit: int = 100) -> pd.DataFrame:
    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/minute"
    response = requests.get(
        url,
        params={"aggregate": aggregate, "limit": min(limit, 1000)},
        headers={"Accept": "application/json"},
        timeout=20,
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
        })

    data = pd.DataFrame(rows)
    return data.dropna().sort_values("time").reset_index(drop=True)


def fetch_live_candles(symbol: str, interval: str = "15min", outputsize: int = 100) -> pd.DataFrame:
    # 1. Specialized Solana DEX Pools (e.g. $ANSEM)
    if symbol in DEX_POOLS:
        network, pool = DEX_POOLS[symbol]
        return fetch_dex_candles(network, pool, aggregate=15, limit=outputsize)

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

    # 3. Twelve Data / Fallback for Forex & Commodities
    load_dotenv()

    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        fallback = Path("data") / f"{symbol.lower()}.csv"
        if fallback.exists():
            return load_price_data(fallback)
        raise RuntimeError("TWELVE_DATA_API_KEY is missing and no fallback CSV exists")

    api_symbol = SYMBOL_ALIASES.get(symbol, symbol.replace("_", "/"))

    for attempt in range(4):
        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": api_symbol,
                "interval": interval,
                "outputsize": outputsize,
                "apikey": api_key,
            },
            timeout=20,
        )

        if response.status_code == 429:
            time.sleep(8 + attempt * 3)
            continue

        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code} from Twelve Data for {symbol}")

        payload = response.json()

        if "values" not in payload:
            message = payload.get("message") or payload.get("status") or str(payload)
            if "minute" in str(message).lower() or "limit" in str(message).lower():
                time.sleep(8 + attempt * 3)
                continue
            raise RuntimeError(f"Twelve Data error for {symbol}: {message}")

        data = pd.DataFrame(payload["values"])
        data = data.rename(columns={"datetime": "time"})
        data["time"] = pd.to_datetime(data["time"], utc=True)

        for column in ["open", "high", "low", "close"]:
            data[column] = pd.to_numeric(data[column], errors="coerce")

        return data[["time", "open", "high", "low", "close"]].dropna().sort_values("time").reset_index(drop=True)

    raise RuntimeError(f"Twelve Data rate limit exceeded for {symbol} after 3 retries")
