import pandas as pd
from bot.strategy import MovingAverageCrossover

def test_strategy_generates_sell_signal_after_cross_down():
    prices = pd.DataFrame({"close": [1, 2, 3, 4, 5, 4, 3, 2, 1]})
    strategy = MovingAverageCrossover(fast_window=2, slow_window=4)
    signals = strategy.generate(prices)
    assert "sell" in set(signals)
