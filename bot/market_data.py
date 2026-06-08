import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from bot.data import load_price_data
from bot.symbols import SYMBOL_ALIASES


def fetch_live_candles(symbol: str, interval: str = "15min", outputsize: int = 100) -> pd.DataFrame:
    load_dotenv()

    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        fallback = Path("data") / f"{symbol.lower()}.csv"
        if fallback.exists():
            return load_price_data(fallback)
        raise RuntimeError("TWELVE_DATA_API_KEY is missing and no fallback CSV exists")

    api_symbol = SYMBOL_ALIASES.get(symbol, symbol.replace("_", "/"))

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
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code} from Twelve Data for {symbol}")

    payload = response.json()

    if "values" not in payload:
        message = payload.get("message") or payload.get("status") or str(payload)
        raise RuntimeError(f"Twelve Data error for {symbol}: {message}")

    data = pd.DataFrame(payload["values"])
    data = data.rename(columns={"datetime": "time"})
    data["time"] = pd.to_datetime(data["time"], utc=True)

    for column in ["open", "high", "low", "close"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    return data[["time", "open", "high", "low", "close"]].dropna().sort_values("time").reset_index(drop=True)
