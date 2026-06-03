from pathlib import Path
import pandas as pd

REQUIRED_COLUMNS = {"time", "open", "high", "low", "close"}

def load_price_data(path: str | Path) -> pd.DataFrame:
    data = pd.read_csv(path)
    missing = REQUIRED_COLUMNS.difference(data.columns)
    if missing:
        raise ValueError(f"Market data is missing required columns: {', '.join(sorted(missing))}")
    data["time"] = pd.to_datetime(data["time"], utc=True)
    return data.sort_values("time").reset_index(drop=True)
