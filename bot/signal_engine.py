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


def adx(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    high = prices["high"]
    low = prices["low"]
    close = prices["close"]

    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    up_move = high - high.shift()
    down_move = low.shift() - low

    plus_dm = pd.Series(0.0, index=prices.index)
    minus_dm = pd.Series(0.0, index=prices.index)

    plus_mask = (up_move > down_move) & (up_move > 0)
    minus_mask = (down_move > up_move) & (down_move > 0)

    plus_dm.loc[plus_mask] = up_move.loc[plus_mask]
    minus_dm.loc[minus_mask] = down_move.loc[minus_mask]

    tr_smoothed = tr.rolling(period).mean()
    plus_dm_smoothed = plus_dm.rolling(period).mean()
    minus_dm_smoothed = minus_dm.rolling(period).mean()

    plus_di = 100 * (plus_dm_smoothed / tr_smoothed.replace(0, pd.NA))
    minus_di = 100 * (minus_dm_smoothed / tr_smoothed.replace(0, pd.NA))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.rolling(period).mean()


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


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
    data["ema100"] = data["close"].ewm(span=100, adjust=False).mean()
    data["ema200"] = data["close"].ewm(span=200, adjust=False).mean()
    data["rsi"] = rsi(data["close"])
    data["atr"] = atr(data)
    data["adx"] = adx(data)
    
    macd_line, signal_line, macd_hist = macd(data["close"])
    data["macd_hist"] = macd_hist

    # Bollinger Bands (20, 2.5 std)
    data["sma20"] = data["close"].rolling(20).mean()
    data["std20"] = data["close"].rolling(20).std()
    data["upper_band"] = data["sma20"] + (2.5 * data["std20"])
    data["lower_band"] = data["sma20"] - (2.5 * data["std20"])

    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    atr_value = float(last["atr"]) if pd.notna(last["atr"]) else close * 0.002
    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else 25.0

    recent = data.iloc[-30:]
    previous = data.iloc[-60:-30]

    recent_high = recent["high"].max()
    recent_low = recent["low"].min()
    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    # Asset-specific Stop Loss and Take Profit Multipliers
    sl_multiplier = 1.5
    tp_multiplier = 3.0

    if symbol == "XAU_USD":
        sl_multiplier = 2.5
        tp_multiplier = 5.0
    elif symbol in ("BTC_USD", "ETH_USD"):
        sl_multiplier = 2.0
        tp_multiplier = 4.0

    # -------------------------------------------------------------
    # STRATEGY 1: London Breakout
    # -------------------------------------------------------------
    london_signal = "HOLD"
    london_reason = ""
    last_time = last["time"]
    last_hour = last_time.hour

    if session_key == "london" or (7 <= last_hour <= 15):
        if last_adx >= 22:
            same_day_mask = (data["time"].dt.date == last_time.date()) & (data["time"].dt.hour >= 0) & (data["time"].dt.hour < 7)
            asian_candles = data.loc[same_day_mask]
            
            if len(asian_candles) >= 4:
                asian_high = asian_candles["high"].max()
                asian_low = asian_candles["low"].min()
                
                if prev["close"] <= asian_high < close and close > last["open"]:
                    london_signal = "BUY"
                    london_reason = f"London Breakout: Price broke above Asian High of {asian_high:.2f} during London Session"
                elif prev["close"] >= asian_low > close and close < last["open"]:
                    london_signal = "SELL"
                    london_reason = f"London Breakout: Price broke below Asian Low of {asian_low:.2f} during London Session"

    # -------------------------------------------------------------
    # STRATEGY 2: Mean Reversion (Bollinger Bands + RSI)
    # -------------------------------------------------------------
    mr_signal = "HOLD"
    mr_reason = ""

    if last_adx < 25:
        if last["low"] <= last["lower_band"] and last["rsi"] <= 35 and candle_rejection(last, "buy"):
            mr_signal = "BUY"
            mr_reason = f"Mean Reversion: Price touched Lower BB ({last['lower_band']:.2f}) and RSI was oversold ({last['rsi']:.1f}) with bullish rejection"
        elif last["high"] >= last["upper_band"] and last["rsi"] >= 65 and candle_rejection(last, "sell"):
            mr_signal = "SELL"
            mr_reason = f"Mean Reversion: Price touched Upper BB ({last['upper_band']:.2f}) and RSI was overbought ({last['rsi']:.1f}) with bearish rejection"

    # -------------------------------------------------------------
    # STRATEGY 3: SMC Liquidity Sweep
    # -------------------------------------------------------------
    sweep_signal = "HOLD"
    sweep_reason = ""

    recent_candles = data.iloc[-31:-1]
    support_level = recent_candles["low"].min()
    resistance_level = recent_candles["high"].max()

    if last["low"] < support_level and close > support_level and candle_rejection(last, "buy"):
        sweep_signal = "BUY"
        sweep_reason = f"Liquidity Sweep: Price swept below key support of {support_level:.2f} and closed back above with bullish rejection"
    elif last["high"] > resistance_level and close < resistance_level and candle_rejection(last, "sell"):
        sweep_signal = "SELL"
        sweep_reason = f"Liquidity Sweep: Price swept above key resistance of {resistance_level:.2f} and closed back below with bearish rejection"

    # -------------------------------------------------------------
    # STRATEGY 4: EMA Ribbon Pullback (MACD Confirmed)
    # -------------------------------------------------------------
    pullback_signal = "HOLD"
    pullback_reason = ""

    if last_adx >= 22:
        is_bullish_trend = last["ema20"] > last["ema50"] > last["ema100"] > last["ema200"]
        is_bearish_trend = last["ema20"] < last["ema50"] < last["ema100"] < last["ema200"]

        if is_bullish_trend:
            pullback_touch = last["low"] <= last["ema50"] and close > last["ema50"]
            if pullback_touch and candle_rejection(last, "buy") and last["macd_hist"] > 0:
                pullback_signal = "BUY"
                pullback_reason = "EMA Pullback: Price pulled back to EMA 50 support in a strong bullish trend with MACD confirmation"
        elif is_bearish_trend:
            pullback_touch = last["high"] >= last["ema50"] and close < last["ema50"]
            if pullback_touch and candle_rejection(last, "sell") and last["macd_hist"] < 0:
                pullback_signal = "SELL"
                pullback_reason = "EMA Pullback: Price pulled back to EMA 50 resistance in a strong bearish trend with MACD confirmation"

    # -------------------------------------------------------------
    # Select Strategy Signal (Prioritized)
    # -------------------------------------------------------------
    strategy_action = "WAIT"
    strategy_reason = ""
    strategy_confidence = 0

    if sweep_signal != "HOLD":
        strategy_action = sweep_signal
        strategy_reason = sweep_reason
        strategy_confidence = 88
    elif london_signal != "HOLD":
        strategy_action = london_signal
        strategy_reason = london_reason
        strategy_confidence = 85
    elif pullback_signal != "HOLD":
        strategy_action = pullback_signal
        strategy_reason = pullback_reason
        strategy_confidence = 82
    elif mr_signal != "HOLD":
        strategy_action = mr_signal
        strategy_reason = mr_reason
        strategy_confidence = 80

    if strategy_action != "WAIT":
        stop_loss = close - (atr_value * sl_multiplier) if strategy_action == "BUY" else close + (atr_value * sl_multiplier)
        take_profit = close + (atr_value * tp_multiplier) if strategy_action == "BUY" else close - (atr_value * tp_multiplier)
        return SignalReport(
            symbol=display_symbol,
            action=strategy_action,
            confidence=strategy_confidence,
            trend="Bullish" if strategy_action == "BUY" else "Bearish",
            entry=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=strategy_reason,
        )

    # -------------------------------------------------------------
    # FALLBACK: Accumulative Checklist Point Scoring
    # -------------------------------------------------------------
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

    # Apply ADX Filter to fallback trend score
    if last_adx < 20:
        long_score -= 15
        short_score -= 15
        long_reasons.append(f"ADX low ({last_adx:.1f})")
        short_reasons.append(f"ADX low ({last_adx:.1f})")
    elif last_adx < 25:
        long_score -= 8
        short_score -= 8
        long_reasons.append(f"ADX weak ({last_adx:.1f})")
        short_reasons.append(f"ADX weak ({last_adx:.1f})")

    if long_score >= min_confidence and long_score >= short_score + 8:
        stop_loss = close - (atr_value * sl_multiplier)
        take_profit = close + (atr_value * tp_multiplier)
        return SignalReport(
            symbol=display_symbol,
            action="BUY",
            confidence=min(long_score, 95),
            trend="Bullish",
            entry=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason="Checklist: " + "; ".join(long_reasons),
        )

    if short_score >= min_confidence and short_score >= long_score + 8:
        stop_loss = close + (atr_value * sl_multiplier)
        take_profit = close - (atr_value * tp_multiplier)
        return SignalReport(
            symbol=display_symbol,
            action="SELL",
            confidence=min(short_score, 95),
            trend="Bearish",
            entry=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason="Checklist: " + "; ".join(short_reasons),
        )

    trend = "Bullish" if long_score > short_score else "Bearish" if short_score > long_score else "Mixed"
    confidence = min(max(long_score, short_score), 95)
    reason = "Setup is not strong enough yet"

    if long_reasons and long_score >= short_score:
        reason = "; ".join(long_reasons)
    elif short_reasons:
        reason = "; ".join(short_reasons)

    return SignalReport(display_symbol, "WAIT", confidence, trend, None, None, None, reason)
