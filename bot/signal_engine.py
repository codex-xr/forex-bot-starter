from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SignalReport:
    symbol: str
    action: str
    confidence: int
    trend: str
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    reason: str

    def to_message(self) -> str:
        if self.action == "WAIT":
            return (
                f"{self.symbol}: WAIT\n"
                f"Trend: {self.trend}\n"
                f"Confidence: {self.confidence}%\n"
                f"Reason: {self.reason}"
            )

        return (
            f"{self.symbol}: {self.action} setup\n"
            f"Trend: {self.trend}\n"
            f"Confidence: {self.confidence}%\n"
            f"Entry: {self.entry:.5f}\n"
            f"Stop Loss: {self.stop_loss:.5f}\n"
            f"Take Profit: {self.take_profit:.5f}\n"
            f"Risk/Reward: about 1:2\n"
            f"Reason: {self.reason}"
        )


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def atr(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = prices["high"] - prices["low"]
    high_close = (prices["high"] - prices["close"].shift()).abs()
    low_close = (prices["low"] - prices["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def analyze_setup(symbol: str, prices: pd.DataFrame, min_confidence: int = 65) -> SignalReport:
    if len(prices) < 60:
        return SignalReport(symbol, "WAIT", 0, "Unknown", None, None, None, "Not enough candle data")

    data = prices.copy()
    data["ema20"] = data["close"].ewm(span=20, adjust=False).mean()
    data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()
    data["rsi"] = rsi(data["close"])
    data["atr"] = atr(data)

    last = data.iloc[-1]
    prev = data.iloc[-2]

    recent_high = data["high"].iloc[-21:-1].max()
    recent_low = data["low"].iloc[-21:-1].min()

    close = float(last["close"])
    atr_value = float(last["atr"]) if pd.notna(last["atr"]) else close * 0.002

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    if close > last["ema20"] > last["ema50"]:
        long_score += 25
        long_reasons.append("EMA trend is bullish")
    if close < last["ema20"] < last["ema50"]:
        short_score += 25
        short_reasons.append("EMA trend is bearish")

    if last["ema20"] > prev["ema20"]:
        long_score += 10
        long_reasons.append("short-term trend is rising")
    if last["ema20"] < prev["ema20"]:
        short_score += 10
        short_reasons.append("short-term trend is falling")

    if 45 <= last["rsi"] <= 68:
        long_score += 15
        long_reasons.append("RSI supports bullish momentum")
    if 32 <= last["rsi"] <= 55:
        short_score += 15
        short_reasons.append("RSI supports bearish momentum")

    if close > recent_high:
        long_score += 25
        long_reasons.append("price broke recent resistance")
    if close < recent_low:
        short_score += 25
        short_reasons.append("price broke recent support")

    if last["low"] <= last["ema20"] and close > last["ema20"]:
        long_score += 15
        long_reasons.append("bullish pullback into EMA")
    if last["high"] >= last["ema20"] and close < last["ema20"]:
        short_score += 15
        short_reasons.append("bearish pullback into EMA")

    if data["close"].iloc[-3:].is_monotonic_increasing:
        long_score += 10
        long_reasons.append("recent candles show buying pressure")
    if data["close"].iloc[-3:].is_monotonic_decreasing:
        short_score += 10
        short_reasons.append("recent candles show selling pressure")

    if long_score >= min_confidence and long_score >= short_score + 10:
        stop_loss = close - (atr_value * 1.5)
        take_profit = close + (atr_value * 3)
        return SignalReport(
            symbol, "BUY", min(long_score, 95), "Bullish", close, stop_loss, take_profit, "; ".join(long_reasons)
        )

    if short_score >= min_confidence and short_score >= long_score + 10:
        stop_loss = close + (atr_value * 1.5)
        take_profit = close - (atr_value * 3)
        return SignalReport(
            symbol, "SELL", min(short_score, 95), "Bearish", close, stop_loss, take_profit, "; ".join(short_reasons)
        )

    trend = "Bullish" if long_score > short_score else "Bearish" if short_score > long_score else "Mixed"
    confidence = min(max(long_score, short_score), 95)
    return SignalReport(
        symbol,
        "WAIT",
        confidence,
        trend,
        None,
        None,
        None,
        "Setup is not strong enough yet",
    )
