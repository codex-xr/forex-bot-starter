from dataclasses import dataclass
import numpy as np
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


@dataclass(frozen=True)
class StrategySignal:
    name: str
    action: str
    confidence: int
    reason: str


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rs = rs.replace([np.inf, -np.inf], 99_999)
    rsi_val = 100 - (100 / (1 + rs))
    both_zero = (gain == 0) & (loss == 0)
    rsi_val[both_zero] = 50.0
    return rsi_val


def atr(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = prices["high"] - prices["low"]
    hc = (prices["high"] - prices["close"].shift()).abs()
    lc = (prices["low"] - prices["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def adx(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    high = prices["high"]
    low = prices["low"]
    close = prices["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    up = high - high.shift()
    down = low.shift() - low
    plus_dm = pd.Series(0.0, index=prices.index)
    minus_dm = pd.Series(0.0, index=prices.index)
    plus_dm.loc[(up > down) & (up > 0)] = up
    minus_dm.loc[(down > up) & (down > 0)] = down

    tr_s = tr.rolling(period).mean()
    pdi = 100 * (plus_dm.rolling(period).mean() / tr_s.replace(0, np.nan))
    mdi = 100 * (minus_dm.rolling(period).mean() / tr_s.replace(0, np.nan))
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.rolling(period).mean()


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    e12 = close.ewm(span=12, adjust=False).mean()
    e26 = close.ewm(span=26, adjust=False).mean()
    line = e12 - e26
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal, line - signal


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["ema100"] = d["close"].ewm(span=100, adjust=False).mean()
    d["ema200"] = d["close"].ewm(span=200, adjust=False).mean()
    d["rsi"] = rsi(d["close"])
    d["atr"] = atr(d)
    d["adx"] = adx(d)
    d["macd_line"], d["macd_signal"], d["macd_hist"] = macd(d["close"])
    d["sma20"] = d["close"].rolling(20).mean()
    d["std20"] = d["close"].rolling(20).std()
    d["upper_band"] = d["sma20"] + 2.5 * d["std20"]
    d["lower_band"] = d["sma20"] - 2.5 * d["std20"]
    return d


# ---------------------------------------------------------------------------
# Rejection candle detection (stricter)
# ---------------------------------------------------------------------------

def check_rejection(row: pd.Series, direction: str) -> bool:
    body = abs(row["close"] - row["open"])
    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]
    total_range = row["high"] - row["low"]

    if total_range == 0 or body == 0:
        return False

    if direction == "buy":
        return (lower_wick > body * 2.0
                and lower_wick >= total_range * 0.4
                and row["close"] > row["open"])
    return (upper_wick > body * 2.0
            and upper_wick >= total_range * 0.4
            and row["close"] < row["open"])


# ---------------------------------------------------------------------------
# Higher-timeframe bias (EMA50 slope over last 5 bars)
# ---------------------------------------------------------------------------

def htf_bias(data: pd.DataFrame) -> str:
    ema50 = data["ema50"]
    slope = ema50.iloc[-1] - ema50.iloc[-5]
    if slope > 0:
        return "bullish"
    if slope < 0:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# SL/TP multipliers per asset class
# ---------------------------------------------------------------------------

def _sl_tp_mult(symbol: str) -> tuple[float, float]:
    if symbol == "XAU_USD":
        return 2.5, 5.0
    if symbol in ("BTC_USD", "ETH_USD"):
        return 2.0, 4.0
    return 1.5, 3.0


# ---------------------------------------------------------------------------
# Strategy 1 — SMC Liquidity Sweep (ranging & trending)
# ---------------------------------------------------------------------------

def _smc_sweep(data: pd.DataFrame) -> StrategySignal:
    last = data.iloc[-1]
    close = float(last["close"])

    ref = data.iloc[-31:-1]
    support = ref["low"].min()
    resistance = ref["high"].max()

    if last["low"] < support and close > support and check_rejection(last, "buy"):
        return StrategySignal(
            "SMC Sweep", "BUY", 88,
            f"Price swept below support ({support:.2f}) and closed back above with rejection",
        )
    if last["high"] > resistance and close < resistance and check_rejection(last, "sell"):
        return StrategySignal(
            "SMC Sweep", "SELL", 88,
            f"Price swept above resistance ({resistance:.2f}) and closed back below with rejection",
        )
    return StrategySignal("SMC Sweep", "HOLD", 0, "")


# ---------------------------------------------------------------------------
# Strategy 2 — London Breakout (trending, ADX >= 22)
# ---------------------------------------------------------------------------

def _london_breakout(data: pd.DataFrame, session_key: str | None) -> StrategySignal:
    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else -1

    is_london = session_key == "london" or (7 <= last["time"].hour <= 15)
    if not is_london or last_adx < 22:
        return StrategySignal("London Breakout", "HOLD", 0, "")

    mask = (
        (data["time"].dt.date == last["time"].date())
        & (data["time"].dt.hour >= 0) & (data["time"].dt.hour < 7)
    )
    asian = data.loc[mask]
    if len(asian) < 4:
        return StrategySignal("London Breakout", "HOLD", 0, "")

    a_high, a_low = asian["high"].max(), asian["low"].min()

    if prev["close"] <= a_high < close and close > last["open"]:
        return StrategySignal("London Breakout", "BUY", 85,
                              f"Broke above Asian High ({a_high:.2f})")
    if prev["close"] >= a_low > close and close < last["open"]:
        return StrategySignal("London Breakout", "SELL", 85,
                              f"Broke below Asian Low ({a_low:.2f})")
    return StrategySignal("London Breakout", "HOLD", 0, "")


# ---------------------------------------------------------------------------
# Strategy 3 — EMA Ribbon Pullback (trending, ADX >= 22)
# ---------------------------------------------------------------------------

def _ema_pullback(data: pd.DataFrame) -> StrategySignal:
    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])

    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else -1
    if last_adx < 22:
        return StrategySignal("EMA Pullback", "HOLD", 0, "")

    bull = last["ema20"] > last["ema50"] > last["ema100"] > last["ema200"]
    bear = last["ema20"] < last["ema50"] < last["ema100"] < last["ema200"]

    if bull and prev["low"] <= last["ema50"] <= close and check_rejection(last, "buy") and last["macd_hist"] > 0:
        return StrategySignal("EMA Pullback", "BUY", 82,
                              "Pullback to EMA 50 in bullish trend, MACD confirming")
    if bear and prev["high"] >= last["ema50"] >= close and check_rejection(last, "sell") and last["macd_hist"] < 0:
        return StrategySignal("EMA Pullback", "SELL", 82,
                              "Pullback to EMA 50 in bearish trend, MACD confirming")
    return StrategySignal("EMA Pullback", "HOLD", 0, "")


# ---------------------------------------------------------------------------
# Strategy 4 — Mean Reversion (ranging, ADX < 22)
# ---------------------------------------------------------------------------

def _mean_reversion(data: pd.DataFrame) -> StrategySignal:
    last = data.iloc[-1]

    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else 999
    if last_adx >= 22:
        return StrategySignal("Mean Reversion", "HOLD", 0, "")

    if last["low"] <= last["lower_band"] and last["rsi"] <= 35 and check_rejection(last, "buy"):
        return StrategySignal("Mean Reversion", "BUY", 80,
                              f"Lower BB touch ({last['lower_band']:.2f}), RSI {last['rsi']:.0f}, bullish rejection")
    if last["high"] >= last["upper_band"] and last["rsi"] >= 65 and check_rejection(last, "sell"):
        return StrategySignal("Mean Reversion", "SELL", 80,
                              f"Upper BB touch ({last['upper_band']:.2f}), RSI {last['rsi']:.0f}, bearish rejection")
    return StrategySignal("Mean Reversion", "HOLD", 0, "")


# ---------------------------------------------------------------------------
# Fallback — 5-factor weighted checklist
# ---------------------------------------------------------------------------

_FACTOR_WEIGHTS = [
    ("trend", 0.30),
    ("structure", 0.25),
    ("momentum", 0.20),
    ("volatility", 0.15),
    ("price_action", 0.10),
]


def _f_trend(data: pd.DataFrame) -> tuple[int, int, list[str], list[str]]:
    last = data.iloc[-1]
    lg, sg = 0, 0
    lr, sr = [], []

    bull_ema = last["close"] > last["ema20"] > last["ema50"]
    bear_ema = last["close"] < last["ema20"] < last["ema50"]
    bull_ribbon = last["ema20"] > last["ema50"] > last["ema100"] > last["ema200"]
    bear_ribbon = last["ema20"] < last["ema50"] < last["ema100"] < last["ema200"]

    if bull_ema:
        lg += 50
        lr.append("bullish EMA 20/50")
    if bear_ema:
        sg += 50
        sr.append("bearish EMA 20/50")
    if bull_ribbon:
        lg += 50
        lr.append("EMA ribbon bull-aligned")
    if bear_ribbon:
        sg += 50
        sr.append("EMA ribbon bear-aligned")

    return lg, sg, lr, sr


def _f_structure(data: pd.DataFrame) -> tuple[int, int, list[str], list[str]]:
    recent = data.iloc[-30:]
    prior = data.iloc[-60:-30]
    lg, sg = 0, 0
    lr, sr = [], []

    if recent["high"].max() > prior["high"].max() and recent["low"].min() > prior["low"].min():
        lg = 100
        lr.append("higher highs/lows")
    if recent["high"].max() < prior["high"].max() and recent["low"].min() < prior["low"].min():
        sg = 100
        sr.append("lower highs/lows")

    return lg, sg, lr, sr


def _f_momentum(data: pd.DataFrame) -> tuple[int, int, list[str], list[str]]:
    last, prev = data.iloc[-1], data.iloc[-2]
    lg, sg = 0, 0
    lr, sr = [], []

    rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50
    if 50 <= rsi <= 70:
        lg += 50
        lr.append(f"RSI {rsi:.0f}")
    if 30 <= rsi <= 50:
        sg += 50
        sr.append(f"RSI {rsi:.0f}")

    macd_rising = last["macd_hist"] > prev["macd_hist"]
    macd_falling = last["macd_hist"] < prev["macd_hist"]
    if last["macd_hist"] > 0 and macd_rising:
        lg += 50
        lr.append("MACD rising bullish")
    if last["macd_hist"] < 0 and macd_falling:
        sg += 50
        sr.append("MACD falling bearish")

    return lg, sg, lr, sr


def _f_volatility(data: pd.DataFrame) -> tuple[int, int, list[str], list[str]]:
    last = data.iloc[-1]
    recent = data.iloc[-30:]
    prior = data.iloc[-60:-30]
    lg, sg = 0, 0
    lr, sr = [], []

    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else 0
    if last_adx >= 25:
        lg += 30
        sg += 30
        lr.append(f"ADX {last_adx:.0f}")
        sr.append(f"ADX {last_adx:.0f}")

    recent_range = recent["high"].max() - recent["low"].min()
    prior_range = prior["high"].max() - prior["low"].min()
    if prior_range > 0 and recent_range < prior_range * 0.7:
        close = float(last["close"])
        if close > recent["high"].iloc[:-1].max():
            lg += 70
            lr.append("compression breakout up")
        elif close < recent["low"].iloc[:-1].min():
            sg += 70
            sr.append("compression breakout down")

    return lg, sg, lr, sr


def _f_price_action(data: pd.DataFrame) -> tuple[int, int, list[str], list[str]]:
    last = data.iloc[-1]
    lg, sg = 0, 0
    lr, sr = [], []

    if check_rejection(last, "buy"):
        lg = 100
        lr.append("bullish rejection")
    elif check_rejection(last, "sell"):
        sg = 100
        sr.append("bearish rejection")

    return lg, sg, lr, sr


_FALLBACK_SCORERS = [_f_trend, _f_structure, _f_momentum, _f_volatility, _f_price_action]


def _fallback_scoring(data: pd.DataFrame) -> tuple[str, int, str, int, str]:
    total_long = 0.0
    total_short = 0.0
    all_lr, all_sr = [], []

    for scorer, (_, weight) in zip(_FALLBACK_SCORERS, _FACTOR_WEIGHTS):
        lg, sg, lr, sr = scorer(data)
        total_long += lg * weight
        total_short += sg * weight
        all_lr.extend(lr)
        all_sr.extend(sr)

    long_score = int(total_long)
    short_score = int(total_short)

    if long_score >= 60 and long_score >= short_score + 8:
        return "BUY", min(long_score, 95), "; ".join(all_lr), long_score, short_score
    if short_score >= 60 and short_score >= long_score + 8:
        return "SELL", min(short_score, 95), "; ".join(all_sr), long_score, short_score

    return "WAIT", max(long_score, short_score), "Setup not strong enough", long_score, short_score


# ---------------------------------------------------------------------------
# Quality gate — picks the best signal from strategies
# ---------------------------------------------------------------------------

def _make_report(symbol: str, action: str, confidence: int, trend: str,
                 close: float, atr_val: float, reason: str) -> SignalReport:
    sl_mult, tp_mult = _sl_tp_mult(symbol)
    if action == "BUY":
        sl = close - atr_val * sl_mult
        tp = close + atr_val * tp_mult
    elif action == "SELL":
        sl = close + atr_val * sl_mult
        tp = close - atr_val * tp_mult
    else:
        sl = tp = None

    return SignalReport(
        symbol=DISPLAY_NAMES.get(symbol, symbol),
        action=action,
        confidence=confidence,
        trend=trend,
        entry=close if action != "WAIT" else None,
        stop_loss=sl,
        take_profit=tp,
        reason=reason,
    )


def _quality_gate(
    strategies: list[StrategySignal],
    bias: str,
    data: pd.DataFrame,
    symbol: str,
    min_confidence: int,
) -> SignalReport:
    last = data.iloc[-1]
    close = float(last["close"])
    atr_val = float(last["atr"]) if pd.notna(last["atr"]) else close * 0.002
    trend = "Bullish" if bias == "bullish" else "Bearish" if bias == "bearish" else "Mixed"

    active = [s for s in strategies if s.action != "HOLD"]
    buys = [s for s in active if s.action == "BUY"]
    sells = [s for s in active if s.action == "SELL"]

    best_buy = max(buys, key=lambda s: s.confidence) if buys else None
    best_sell = max(sells, key=lambda s: s.confidence) if sells else None

    for cand, direction in [(best_buy, "BUY"), (best_sell, "SELL")]:
        if cand is None:
            continue
        same = buys if direction == "BUY" else sells

        # High confidence with HTF alignment
        if cand.confidence >= 85 and bias == direction.lower():
            return _make_report(symbol, direction, cand.confidence, trend, close, atr_val, cand.reason)

        # Two different strategies agreeing
        if len(same) >= 2 and len({s.name for s in same}) >= 2:
            avg = sum(s.confidence for s in same) // len(same)
            reason = " | ".join(s.reason for s in same)
            return _make_report(symbol, direction, avg, trend, close, atr_val, reason)

    # Fallback to scoring
    fb_action, fb_conf, fb_reason, long_s, short_s = _fallback_scoring(data)
    if fb_action != "WAIT" and fb_conf >= min_confidence:
        return _make_report(symbol, fb_action, fb_conf, trend, close, atr_val, fb_reason)

    # Nothing qualified → WAIT
    fallback_conf = max(long_s, short_s)
    return _make_report(symbol, "WAIT", fallback_conf, trend, close, atr_val, fb_reason)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_setup(
    symbol: str,
    prices: pd.DataFrame,
    min_confidence: int = 60,
    session_key: str | None = None,
) -> SignalReport:
    if len(prices) < 80:
        return SignalReport(
            DISPLAY_NAMES.get(symbol, symbol),
            "WAIT", 0, "Unknown", None, None, None,
            "Not enough candle data",
        )

    data = compute_indicators(prices)
    bias = htf_bias(data)

    strategies = [
        _smc_sweep(data),
        _london_breakout(data, session_key),
        _ema_pullback(data),
        _mean_reversion(data),
    ]

    return _quality_gate(strategies, bias, data, symbol, min_confidence)
