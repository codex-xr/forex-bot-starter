import os
from dataclasses import dataclass
from dotenv import load_dotenv

@dataclass(frozen=True)
class Settings:
    mode: str
    symbol: str
    account_balance: float
    risk_per_trade: float
    max_open_trades: int
    fast_ma: int
    slow_ma: int

def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        mode=os.getenv("BOT_MODE", "backtest"),
        symbol=os.getenv("SYMBOL", "EUR_USD"),
        account_balance=float(os.getenv("ACCOUNT_BALANCE", "10000")),
        risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
        max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "1")),
        fast_ma=int(os.getenv("FAST_MA", "5")),
        slow_ma=int(os.getenv("SLOW_MA", "20")),
    )
