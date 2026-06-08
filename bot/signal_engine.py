from dataclasses import dataclass

import pandas as pd

from bot.symbols import DISPLAY_NAMES


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


def candle_rejection(row: pd.Series, direction: str) -> bool:
    body = abs(row["close"] - row["open"])
    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]

    if direction == "buy":
        return lower_wick > body * 1.2 and row["close"] > row["open"]
    return upper_wick > body * 1.2 and row["close"] < row["open"]


def analyze_setup(
    symbol: str,
    prices: pd.DataFrame,
    min_confidence: int = 60,
    session_key: str | None = None,
) -> SignalReport:
    display_symbol = DISPLAY_NAMES.get(symbol, symbol)

    if len(prices) < 80:
        return SignalReport(display_symbol, "WAIT", 0, "Unknown", None, None, None, "Not enough candle data")

    data = prices.copy()
    data["ema20"] = data["close"].ewm(span=20, adjust=False).mean()
    data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()
    data["rsi"] = rsi(data["close"])
    data["atr"] = atr(data)

    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    atr_value = float(last["atr"]) if pd.notna(last["atr"]) else close * 0.002

    recent = data.iloc[-30:]
    previous = data.iloc[-60:-30]

    recent_high = recent["high"].max()
    recent_low = recent["low"].min()
    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    # 1. EMA 20/50 trend.
    if close > last["ema20"] > last["ema50"]:
        long_score += 15
        long_reasons.append("EMA 20/50 trend is bullish")
    if close < last["ema20"] < last["ema50"]:
        short_score += 15
        short_reasons.append("EMA 20/50 trend is bearish")

    # 2. Market structure: higher highs/higher lows or lower highs/lower lows.
    if recent_high > previous_high and recent_low > previous_low:
        long_score += 18
        long_reasons.append("market structure shows higher highs and higher lows")
    if recent_high < previous_high and recent_low < previous_low:
        short_score += 18
        short_reasons.append("market structure shows lower highs and lower lows")

    # 3. Pullback + trend continuation.
    if last["low"] <= last["ema20"] and close > last["ema20"] and candle_rejection(last, "buy"):
        long_score += 18
        long_reasons.append("pullback into EMA support with bullish rejection")
    if last["high"] >= last["ema20"] and close < last["ema20"] and candle_rejection(last, "sell"):
        short_score += 18
        short_reasons.append("pullback into EMA resistance with bearish rejection")

    # 4. Break and retest.
    resistance = data["high"].iloc[-40:-5].max()
    support = data["low"].iloc[-40:-5].min()

    if data["close"].iloc[-5:-1].max() > resistance and last["low"] <= resistance <= close:
        long_score += 16
        long_reasons.append("break and retest of resistance")
    if data["close"].iloc[-5:-1].min() < support and last["high"] >= support >= close:
        short_score += 16
        short_reasons.append("break and retest of support")

    # 5. Liquidity sweep reversal.
    if last["low"] < support and close > support and candle_rejection(last, "buy"):
        long_score += 18
        long_reasons.append("liquidity sweep below support with bullish reversal")
    if last["high"] > resistance and close < resistance and candle_rejection(last, "sell"):
        short_score += 18
        short_reasons.append("liquidity sweep above resistance with bearish reversal")

    # 6. Higher timeframe bias approximated with slow EMA.
    if close > data["ema50"].iloc[-1] and data["ema50"].iloc[-1] > data["ema50"].iloc[-10]:
        long_score += 12
        long_reasons.append("higher timeframe bias is bullish")
    if close < data["ema50"].iloc[-1] and data["ema50"].iloc[-1] < data["ema50"].iloc[-10]:
        short_score += 12
        short_reasons.append("higher timeframe bias is bearish")

    # 7. Supply and demand reaction.
    impulse_size = data["close"].diff().abs()
    impulse_threshold = impulse_size.rolling(30).mean().iloc[-1] * 1.8

    if abs(prev["close"] - prev["open"]) > impulse_threshold:
        if prev["close"] > prev["open"] and last["low"] <= prev["open"] <= close:
            long_score += 12
            long_reasons.append("reaction from demand zone")
        if prev["close"] < prev["open"] and last["high"] >= prev["open"] >= close:
            short_score += 12
            short_reasons.append("reaction from supply zone")

    # 8. London open expansion.
    if session_key == "london":
        if close > recent["close"].iloc[-10:].mean() and last["rsi"] > 55:
            long_score += 10
            long_reasons.append("London open expansion supports bullish momentum")
        if close < recent["close"].iloc[-10:].mean() and last["rsi"] < 45:
            short_score += 10
            short_reasons.append("London open expansion supports bearish momentum")

    # 9. New York continuation.
    if session_key == "new_york":
        london_direction = data["close"].iloc[-20] < data["close"].iloc[-5]
        if london_direction and close > last["ema20"]:
            long_score += 10
            long_reasons.append("New York continuation of prior bullish direction")
        if not london_direction and close < last["ema20"]:
            short_score += 10
            short_reasons.append("New York continuation of prior bearish direction")

    # 10. Volatility compression breakout.
    recent_range = recent["high"].max() - recent["low"].min()
    prior_range = previous["high"].max() - previous["low"].min()

    if prior_range > 0 and recent_range < prior_range * 0.7:
        if close > recent["high"].iloc[:-1].max():
            long_score += 14
            long_reasons.append("volatility compression breakout upward")
        if close < recent["low"].iloc[:-1].min():
            short_score += 14
            short_reasons.append("volatility compression breakout downward")

    # 11. RSI momentum confirmation.
    if 50 <= last["rsi"] <= 70:
        long_score += 10
        long_reasons.append("RSI supports bullish momentum")
    if 30 <= last["rsi"] <= 50:
        short_score += 10
        short_reasons.append("RSI supports bearish momentum")

    # 12. Recent candle pressure.
    if data["close"].iloc[-3:].is_monotonic_increasing:
        long_score += 8
        long_reasons.append("recent candles show buying pressure")
    if data["close"].iloc[-3:].is_monotonic_decreasing:
        short_score += 8
        short_reasons.append("recent candles show selling pressure")

    if long_score >= min_confidence and long_score >= short_score + 8:
        stop_loss = close - (atr_value * 1.5)
        take_profit = close + (atr_value * 3)
        return SignalReport(
            symbol,
            "BUY",
            min(long_score, 95),
            "Bullish",
            close,
            stop_loss,
            take_profit,
            "; ".join(long_reasons),
        )

    if short_score >= min_confidence and short_score >= long_score + 8:
        stop_loss = close + (atr_value * 1.5)
        take_profit = close - (atr_value * 3)
        return SignalReport(
            symbol,
            "SELL",
            min(short_score, 95),
            "Bearish",
            close,
            stop_loss,
            take_profit,
            "; ".join(short_reasons),
        )

    trend = "Bullish" if long_score > short_score else "Bearish" if short_score > long_score else "Mixed"
    confidence = min(max(long_score, short_score), 95)
    reason = "Setup is not strong enough yet"

    if long_reasons and long_score >= short_score:
        reason = "; ".join(long_reasons)
    elif short_reasons:
        reason = "; ".join(short_reasons)

    return SignalReport(display_symbol, "WAIT", confidence, trend, None, None, None, reason)
