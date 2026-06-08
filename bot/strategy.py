from dataclasses import dataclass
from enum import Enum
import pandas as pd

class Signal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"

@dataclass(frozen=True)
class MovingAverageCrossover:
    fast_window: int = 5
    slow_window: int = 20

    def __post_init__(self):
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("Moving-average windows must be positive")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")

    def generate(self, prices: pd.DataFrame) -> pd.Series:
        fast = prices["close"].rolling(self.fast_window).mean()
        slow = prices["close"].rolling(self.slow_window).mean()

        buy = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
        sell = (fast.shift(1) >= slow.shift(1)) & (fast < slow)

        signals = pd.Series(Signal.HOLD.value, index=prices.index)
        signals.loc[buy] = Signal.BUY.value
        signals.loc[sell] = Signal.SELL.value
        return signals
