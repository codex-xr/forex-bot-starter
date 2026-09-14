from dataclasses import dataclass
import html
import os
import numpy as np
import pandas as pd

from bot.symbols import DISPLAY_NAMES

USE_LIMIT_ORDERS = os.getenv("USE_LIMIT_ORDERS", "true").lower() in ("true", "1", "yes")


def _fmt_price(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1000:
        return f"<code>{val:.2f}</code>"
    if abs(val) >= 10:
        return f"<code>{val:.3f}</code>"
    if abs(val) >= 0.1:
        return f"<code>{val:.5f}</code>"
    if abs(val) >= 0.0001:
        return f"<code>{val:.8f}</code>"
    return f"<code>{val:.10f}</code>"


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
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    catalyst: str | None = None
    smart_money: str | None = None
    order_type: str = "LIMIT"
    market_price: float | None = None

    def to_message(self) -> str:
        safe_reason = html.escape(self.reason)
        safe_trend = html.escape(self.trend)
        smart_money_block = f"\n🏛️ <b>Binance Smart Money:</b>\n{self.smart_money}\n" if self.smart_money else ""

        if self.action == "WAIT":
            return (
                f"<b>{self.symbol}</b>: <code>WAIT</code>\n"
                f"Trend: {safe_trend}\n"
                f"Confidence: <code>{self.confidence}%</code>\n"
                f"{smart_money_block}"
                f"Reason: {safe_reason}"
            )

        tp1_val = self.tp1 or self.take_profit
        tp2_val = self.tp2 or self.take_profit
        tp3_val = self.tp3 or self.take_profit

        catalyst_line = ""
        if self.catalyst:
            safe_cat = html.escape(self.catalyst)
            catalyst_line = f"📰 <b>News Catalyst:</b> <i>{safe_cat}</i>\n"

        if self.order_type == "LIMIT" and self.entry is not None:
            order_line = (
                f"⚡ <b>ORDER TYPE:</b> <b>{self.action} LIMIT</b>\n"
                f"📍 <b>LIMIT ENTRY:</b> {_fmt_price(self.entry)}\n"
            )
            market_line = (
                f"📊 <b>Current Price:</b> {_fmt_price(self.market_price or self.entry)} <i>(Waiting for retest)</i>\n"
                f"⏳ <b>Rule:</b> Cancel if TP1 reached before limit entry\n"
            )
        else:
            order_line = (
                f"⚡ <b>Action:</b> Instant Market <b>{self.action}</b> (or <b>{self.action} STOP</b> at {_fmt_price(self.entry)})\n"
                f"Entry: {_fmt_price(self.entry)}\n"
                if self.entry is not None else ""
            )
            market_line = ""

        return (
            f"🚨 <b>VIP SIGNAL: {self.symbol}</b> 🚨\n"
            f"<b>{self.symbol}</b>: <b>{self.action} SETUP</b>\n"
            f"Trend: {safe_trend}\n"
            f"Confidence: <code>{self.confidence}%</code>\n"
            f"{order_line}"
            f"Stop Loss: {_fmt_price(self.stop_loss)}\n"
            f"TP 1 (1:1.0): {_fmt_price(tp1_val)} <i>(Pure 1:1 Target)</i>\n"
            f"TP 2 (1:2.0): {_fmt_price(tp2_val)} <i>(Runner Target)</i>\n"
            f"TP 3 (1:3.5): {_fmt_price(tp3_val)} <i>(Max Extension)</i>\n"
            f"{market_line}"
            f"{catalyst_line}"
            f"{smart_money_block}"
            f"Reason: {safe_reason}"
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
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    rsi_val[both_zero] = 50.0
    return rsi_val


def atr(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = prices["high"] - prices["low"]
    hc = (prices["high"] - prices["close"].shift()).abs()
    lc = (prices["low"] - prices["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


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
    if "volume" in d.columns:
        vol_ma = d["volume"].rolling(20).mean().replace(0, np.nan)
        d["rvol"] = (d["volume"] / vol_ma).fillna(1.0)
    else:
        d["rvol"] = 1.0
    return d


def check_rejection(row: pd.Series, direction: str) -> bool:
    body = abs(row["close"] - row["open"])
    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]
    total_range = row["high"] - row["low"]

    if total_range == 0:
        return False

    if direction == "buy":
        is_standard_hammer = (
            body > 0
            and lower_wick > body * 1.8
            and lower_wick >= total_range * 0.35
            and row["close"] > row["open"]
        )
        is_pinbar_hammer = (
            lower_wick >= total_range * 0.55
            and upper_wick <= total_range * 0.20
        )
        return bool(is_standard_hammer or is_pinbar_hammer)

    is_standard_star = (
        body > 0
        and upper_wick > body * 1.8
        and upper_wick >= total_range * 0.35
        and row["close"] < row["open"]
    )
    is_pinbar_star = (
        upper_wick >= total_range * 0.55
        and lower_wick <= total_range * 0.20
    )
    return bool(is_standard_star or is_pinbar_star)


def htf_bias(data: pd.DataFrame) -> str:
    if "ema50" not in data.columns:
        return "neutral"
    ema50 = data["ema50"]
    slope = ema50.iloc[-1] - ema50.iloc[-5] if len(ema50) >= 5 else 0.0
    if "ema200" in data.columns and len(data) >= 20:
        close = float(data["close"].iloc[-1])
        ema200 = float(data["ema200"].iloc[-1])
        if close > ema200 and slope > 0:
            return "bullish"
        if close < ema200 and slope < 0:
            return "bearish"
        return "neutral"
    if slope > 0:
        return "bullish"
    if slope < 0:
        return "bearish"
    return "neutral"


# ===========================================================================
# 🚀 PART 1: CRYPTO & MEMECOIN STRATEGY ENGINE (100% UNTOUCHED & ISOLATED)
# ===========================================================================

CRYPTO_SYMBOLS = {
    # Major Cryptos (/c1)
    "BTC_USD", "ETH_USD", "SOL_USD", "XRP_USD", "DOGE_USD", "ADA_USD",
    # High-Momentum Altcoins (/c2)
    "BNB_USD", "AVAX_USD", "LINK_USD", "SUI_USD", "NEAR_USD", "LTC_USD",
    # Top Memecoins (/m1)
    "WIF_USD", "PEPE_USD", "SHIB_USD", "BONK_USD", "FLOKI_USD", "BRETT_USD", "ANSEM_USD",
    # Trending & Narrative Memecoins (/m2)
    "TRUMP_USD", "BOME_USD", "PENGU_USD", "MOG_USD", "PEOPLE_USD", "ELON_USD",
}

CRYPTO_LARGE_CAP = {"BTC_USD", "ETH_USD"}
CRYPTO_MID_CAP = {"SOL_USD", "XRP_USD", "ADA_USD", "BNB_USD", "AVAX_USD", "LINK_USD", "SUI_USD", "NEAR_USD", "LTC_USD"}
CRYPTO_MEME = {
    "DOGE_USD", "WIF_USD", "PEPE_USD", "SHIB_USD", "BONK_USD", "FLOKI_USD",
    "BRETT_USD", "ANSEM_USD", "TRUMP_USD", "BOME_USD", "PENGU_USD", "MOG_USD",
    "PEOPLE_USD", "ELON_USD",
}


def _sl_tp_mult(symbol: str) -> tuple[float, float]:
    if symbol in ("XAU_USD", "US30"):
        return 2.0, 4.0
    if symbol in CRYPTO_LARGE_CAP:
        return 2.0, 3.0  # BTC/ETH: tighter institutional stops
    if symbol in CRYPTO_MID_CAP:
        return 2.5, 3.5  # Mid-caps: standard
    if symbol in CRYPTO_MEME:
        return 3.5, 5.0  # Memecoins: wider stops, bigger runners
    if symbol in CRYPTO_SYMBOLS:
        return 2.5, 3.5  # Fallback for any new crypto
    return 1.8, 3.5


def _crypto_momentum_surge(data: pd.DataFrame, symbol: str) -> StrategySignal:
    if symbol not in CRYPTO_SYMBOLS:
        return StrategySignal("Crypto Momentum Surge", "HOLD", 0, "")

    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    open_p = float(last["open"])
    body = abs(close - open_p)
    total_range = float(last["high"] - last["low"])
    last_rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50
    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else 0

    if last_adx < 18 or total_range == 0:
        return StrategySignal("Crypto Momentum Surge", "HOLD", 0, "")

    ref = data.iloc[-21:-1]
    prior_high = ref["high"].max()
    prior_low = ref["low"].min()

    last_rvol = float(last["rvol"]) if "rvol" in last and pd.notna(last["rvol"]) else 1.2

    has_ema200 = "ema200" in last and pd.notna(last["ema200"])
    macro_bull = (close >= last["ema200"] * 0.998 and last["ema50"] >= last["ema200"] * 0.995) if has_ema200 else True
    macro_bear = (close <= last["ema200"] * 1.002 and last["ema50"] <= last["ema200"] * 1.005) if has_ema200 else True

    bull_trend = macro_bull and last["ema20"] > last["ema50"] and close > last["ema20"] * 0.998
    bear_trend = macro_bear and last["ema20"] < last["ema50"] and close < last["ema20"] * 1.002

    if bull_trend and 48 <= last_rsi <= 75 and last["macd_hist"] > 0:
        is_breakout = close > prior_high and close > open_p and body >= 0.35 * total_range and last_rvol >= 1.20 and last_adx >= 20
        is_ema_bounce = (
            prev["low"] <= last["ema20"] * 1.003
            and close >= last["ema20"]
            and last_adx >= 20
            and (check_rejection(last, "buy") or (close > open_p and body >= 0.30 * total_range))
        )

        if is_breakout:
            rvol_str = f", RVol {last_rvol:.1f}x" if last_rvol > 1.0 else ""
            return StrategySignal(
                "Crypto Momentum Surge", "BUY", 92,
                f"High-volume breakout ({close:.4f} > {prior_high:.4f}), RSI {last_rsi:.0f}, ADX {last_adx:.0f}{rvol_str}",
            )
        if is_ema_bounce:
            return StrategySignal(
                "Crypto Momentum Surge", "BUY", 85,
                f"EMA 20 dynamic support bounce, RSI {last_rsi:.0f}, strong trend continuation",
            )

    if bear_trend and 25 <= last_rsi <= 52 and last["macd_hist"] < 0:
        is_breakdown = close < prior_low and close < open_p and body >= 0.35 * total_range and last_rvol >= 1.20 and last_adx >= 20
        is_ema_reject = (
            prev["high"] >= last["ema20"] * 0.997
            and close <= last["ema20"]
            and last_adx >= 20
            and (check_rejection(last, "sell") or (close < open_p and body >= 0.30 * total_range))
        )

        if is_breakdown:
            rvol_str = f", RVol {last_rvol:.1f}x" if last_rvol > 1.0 else ""
            return StrategySignal(
                "Crypto Momentum Surge", "SELL", 92,
                f"High-volume breakdown ({close:.4f} < {prior_low:.4f}), RSI {last_rsi:.0f}, ADX {last_adx:.0f}{rvol_str}",
            )
        if is_ema_reject:
            return StrategySignal(
                "Crypto Momentum Surge", "SELL", 85,
                f"EMA 20 dynamic resistance rejection, RSI {last_rsi:.0f}, strong trend continuation",
            )

    return StrategySignal("Crypto Momentum Surge", "HOLD", 0, "")


def _news_catalyst_momentum(data: pd.DataFrame, symbol: str) -> StrategySignal:
    try:
        from bot.news_engine import get_asset_catalyst
        cat_info = get_asset_catalyst(symbol)
    except Exception:
        cat_info = None

    if not cat_info:
        return StrategySignal("News Catalyst Momentum", "HOLD", 0, "")

    sentiment_label, score, headline = cat_info
    last = data.iloc[-1]
    close = float(last["close"])
    ema20 = float(last["ema20"])
    rsi_v = float(last["rsi"]) if pd.notna(last["rsi"]) else 50
    adx_v = float(last["adx"]) if pd.notna(last["adx"]) else 20

    if sentiment_label == "Bullish" and close >= ema20 and rsi_v >= 48 and adx_v >= 18:
        conf = min(95, 86 + abs(score) // 10)
        return StrategySignal(
            "News Catalyst Momentum", "BUY", conf,
            f"Bullish news catalyst: '{headline[:60]}...' confirmed by EMA20 breakout (RSI {rsi_v:.0f}, ADX {adx_v:.0f})",
        )

    if sentiment_label == "Bearish" and close <= ema20 and rsi_v <= 52 and adx_v >= 18:
        conf = min(95, 86 + abs(score) // 10)
        return StrategySignal(
            "News Catalyst Momentum", "SELL", conf,
            f"Bearish news catalyst: '{headline[:60]}...' confirmed by EMA20 breakdown (RSI {rsi_v:.0f}, ADX {adx_v:.0f})",
        )

    return StrategySignal("News Catalyst Momentum", "HOLD", 0, "")


def _mean_reversion(data: pd.DataFrame, symbol: str = "") -> StrategySignal:
    last = data.iloc[-1]
    last_adx = float(last["adx"]) if pd.notna(last["adx"]) else 999
    # Crypto has higher baseline volatility; allow mean reversion up to ADX 28
    adx_ceiling = 28 if symbol in CRYPTO_SYMBOLS else 22
    if last_adx >= adx_ceiling:
        return StrategySignal("Mean Reversion", "HOLD", 0, "")

    if last["low"] <= last["lower_band"] and last["rsi"] <= 38 and check_rejection(last, "buy"):
        return StrategySignal("Mean Reversion", "BUY", 78,
                              f"Lower BB touch ({last['lower_band']:.2f}), RSI {last['rsi']:.0f}, bullish rejection")
    if last["high"] >= last["upper_band"] and last["rsi"] >= 62 and check_rejection(last, "sell"):
        return StrategySignal("Mean Reversion", "SELL", 78,
                              f"Upper BB touch ({last['upper_band']:.2f}), RSI {last['rsi']:.0f}, bearish rejection")
    return StrategySignal("Mean Reversion", "HOLD", 0, "")


def _smc_sweep(data: pd.DataFrame, bias: str = "neutral", symbol: str = "") -> StrategySignal:
    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])

    ref = data.iloc[-31:-1]
    support = ref["low"].min()
    resistance = ref["high"].max()

    if symbol in CRYPTO_SYMBOLS:
        last_adx = float(last["adx"]) if pd.notna(last["adx"]) else 0
        # Only block counter-trend sweeps in strong trends (ADX >= 30)
        if last_adx >= 30:
            if bias == "bearish":
                allow_buy, allow_sell = False, True
            elif bias == "bullish":
                allow_buy, allow_sell = True, False
            else:
                allow_buy = allow_sell = True
        else:
            allow_buy = allow_sell = True
    else:
        allow_buy = allow_sell = True

    macd_turning_up = last["macd_hist"] >= prev["macd_hist"]
    macd_turning_down = last["macd_hist"] <= prev["macd_hist"]

    if allow_buy and last["low"] < support and close > support and check_rejection(last, "buy") and macd_turning_up:
        return StrategySignal(
            "SMC Sweep", "BUY", 88,
            f"Price swept below support ({support:.2f}) and closed back above with rejection & MACD slowing",
        )
    if allow_sell and last["high"] > resistance and close < resistance and check_rejection(last, "sell") and macd_turning_down:
        return StrategySignal(
            "SMC Sweep", "SELL", 88,
            f"Price swept above resistance ({resistance:.2f}) and closed back below with rejection & MACD slowing",
        )
    return StrategySignal("SMC Sweep", "HOLD", 0, "")


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

    rsi_v = float(last["rsi"]) if pd.notna(last["rsi"]) else 50
    if 50 <= rsi_v <= 70:
        lg += 50
        lr.append(f"RSI {rsi_v:.0f}")
    if 30 <= rsi_v <= 50:
        sg += 50
        sr.append(f"RSI {rsi_v:.0f}")

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
    if check_rejection(last, "sell"):
        sg = 100
        sr.append("bearish rejection")

    return lg, sg, lr, sr


_FALLBACK_SCORERS = [_f_trend, _f_structure, _f_momentum, _f_volatility, _f_price_action]


def _fallback_scoring(data: pd.DataFrame) -> tuple[str, int, str, int, int]:
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


def _make_report(symbol: str, action: str, confidence: int, trend: str,
                 close: float, atr_val: float, reason: str,
                 data: pd.DataFrame | None = None,
                 catalyst: str | None = None) -> SignalReport:
    sl_mult, tp_mult = _sl_tp_mult(symbol)
    # Calibrate risk distance for 25x leverage (max 2.0% - 2.5% price distance)
    max_risk_pct = 0.025 if symbol in CRYPTO_MEME else 0.020
    max_risk_dist = close * max_risk_pct

    if action == "BUY":
        if data is not None and len(data) >= 15:
            swing_low = float(data.iloc[-21:-1]["low"].min())
            sl = min(close - atr_val * sl_mult, swing_low - atr_val * 0.5)
        else:
            sl = close - atr_val * sl_mult
        # 25x leverage risk clamp
        sl = max(sl, close - max_risk_dist)

        if USE_LIMIT_ORDERS:
            pullback = max(0.20 * atr_val, close * 0.0025)
            limit_entry = close - pullback
            if limit_entry <= sl:
                limit_entry = close - (close - sl) * 0.3
            risk = max(limit_entry - sl, atr_val * 0.8)
            tp1 = limit_entry + risk * 1.0  # Pure 1:1 TP1
            tp2 = limit_entry + risk * (tp_mult / sl_mult)
            tp3 = limit_entry + risk * 4.0
            entry = limit_entry
            order_type = "LIMIT"
        else:
            risk = max(close - sl, atr_val * 0.8)
            tp1 = close + risk * 1.0  # Pure 1:1 TP1
            tp2 = close + risk * (tp_mult / sl_mult)
            tp3 = close + risk * 4.0
            entry = close
            order_type = "MARKET"
        tp = tp1
    elif action == "SELL":
        if data is not None and len(data) >= 15:
            swing_high = float(data.iloc[-21:-1]["high"].max())
            sl = max(close + atr_val * sl_mult, swing_high + atr_val * 0.5)
        else:
            sl = close + atr_val * sl_mult
        # 25x leverage risk clamp
        sl = min(sl, close + max_risk_dist)

        if USE_LIMIT_ORDERS:
            pullback = max(0.20 * atr_val, close * 0.0025)
            limit_entry = close + pullback
            if limit_entry >= sl:
                limit_entry = close + (sl - close) * 0.3
            risk = max(sl - limit_entry, atr_val * 0.8)
            tp1 = limit_entry - risk * 1.0  # Pure 1:1 TP1
            tp2 = limit_entry - risk * (tp_mult / sl_mult)
            tp3 = limit_entry - risk * 4.0
            entry = limit_entry
            order_type = "LIMIT"
        else:
            risk = max(sl - close, atr_val * 0.8)
            tp1 = close - risk * 1.0  # Pure 1:1 TP1
            tp2 = close - risk * (tp_mult / sl_mult)
            tp3 = close - risk * 4.0
            entry = close
            order_type = "MARKET"
        tp = tp1
    else:
        sl = tp = tp1 = tp2 = tp3 = None
        entry = None
        order_type = "LIMIT" if USE_LIMIT_ORDERS else "MARKET"

    return SignalReport(
        symbol=DISPLAY_NAMES.get(symbol, symbol),
        action=action,
        confidence=confidence,
        trend=trend,
        entry=entry if action != "WAIT" else None,
        stop_loss=sl,
        take_profit=tp,
        reason=reason,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        catalyst=catalyst,
        order_type=order_type,
        market_price=close if action != "WAIT" else None,
    )


def _quality_gate(
    strategies: list[StrategySignal],
    bias: str,
    data: pd.DataFrame,
    symbol: str,
    min_confidence: int = 65,
) -> SignalReport:
    last = data.iloc[-1]
    close = float(last["close"])
    atr_val = float(last["atr"]) if pd.notna(last["atr"]) else 0.0010
    trend = "Bullish" if bias == "bullish" else ("Bearish" if bias == "bearish" else "Ranging")

    buys = [s for s in strategies if s.action == "BUY"]
    sells = [s for s in strategies if s.action == "SELL"]

    best_buy = max(buys, key=lambda s: s.confidence) if buys else None
    best_sell = max(sells, key=lambda s: s.confidence) if sells else None

    gate_threshold = min_confidence if (min_confidence is not None and min_confidence > 0) else 65

    # Prioritize direction with highest confidence!
    candidates = []
    if best_buy is not None:
        candidates.append((best_buy, "BUY", buys))
    if best_sell is not None:
        candidates.append((best_sell, "SELL", sells))
    candidates.sort(key=lambda item: item[0].confidence, reverse=True)

    for cand, direction, same in candidates:
        catalyst = None
        for s in same:
            if s.name == "News Catalyst Momentum":
                catalyst = s.reason.split("confirmed by")[0].strip()

        # Two different strategies agreeing (Confluence)
        unique_strategies = {s.name for s in same}
        if len(unique_strategies) >= 2:
            base_conf = max(s.confidence for s in same)
            confluence_bonus = min(4 * (len(unique_strategies) - 1), 8)
            compounded_conf = min(98, base_conf + confluence_bonus)
            if compounded_conf >= gate_threshold:
                reason = " | ".join(s.reason for s in same)
                return _make_report(symbol, direction, compounded_conf, trend, close, atr_val, reason, data=data, catalyst=catalyst)

        # Quality Gate threshold
        if cand.confidence >= gate_threshold:
            return _make_report(symbol, direction, cand.confidence, trend, close, atr_val, cand.reason, data=data, catalyst=catalyst)

    fb_action, fb_conf, fb_reason, long_s, short_s = _fallback_scoring(data)
    if fb_action != "WAIT" and fb_conf >= gate_threshold:
        return _make_report(symbol, fb_action, fb_conf, trend, close, atr_val, fb_reason, data=data)

    fallback_conf = max(long_s, short_s)
    return _make_report(symbol, "WAIT", fallback_conf, trend, close, atr_val, fb_reason, data=data)


def _analyze_crypto_setup(
    symbol: str,
    data: pd.DataFrame,
    bias: str,
    min_confidence: int = 65,
) -> SignalReport:
    """
    Dedicated Crypto & Memecoin Trading Pipeline enhanced with
    Binance Smart Money Confluence (Taker Volume Delta, Open Interest, Funding Rate, Top Trader Whales).
    """
    # 1. Query Binance Smart Money Metrics
    sm_metrics = None
    try:
        from bot.binance_engine import analyze_binance_smart_money
        sm_metrics = analyze_binance_smart_money(symbol, data)
    except Exception as sm_err:
        print(f"[SignalEngine] Binance smart money error for {symbol}: {sm_err}")

    strategies = [
        _smc_sweep(data, bias, symbol),
        _mean_reversion(data, symbol),
        _crypto_momentum_surge(data, symbol),
        _news_catalyst_momentum(data, symbol),
    ]

    report = _quality_gate(strategies, bias, data, symbol, min_confidence)

    # 2. Active Smart Money VETO: Protect capital if whales or funding directly contradict the signal
    if sm_metrics and report.action in ("BUY", "SELL"):
        is_vetoed, veto_reason = sm_metrics.is_vetoed(report.action)
        if is_vetoed:
            return SignalReport(
                symbol=report.symbol,
                action="WAIT",
                confidence=45,
                trend=report.trend,
                entry=None,
                stop_loss=None,
                take_profit=None,
                reason=f"⛔ {veto_reason} (Signal vetoed to protect capital)",
                smart_money=sm_metrics.summary_text,
            )

    # 3. Layer Smart Money Confluence onto report
    if sm_metrics and sm_metrics.summary_text:
        conf = report.confidence
        if report.action == "BUY" and sm_metrics.bias == "BULLISH":
            conf = min(98, conf + sm_metrics.confidence_modifier)
        elif report.action == "SELL" and sm_metrics.bias == "BEARISH":
            conf = min(98, conf + sm_metrics.confidence_modifier)

        return SignalReport(
            symbol=report.symbol,
            action=report.action,
            confidence=conf,
            trend=report.trend,
            entry=report.entry,
            stop_loss=report.stop_loss,
            take_profit=report.take_profit,
            reason=report.reason,
            tp1=report.tp1,
            tp2=report.tp2,
            tp3=report.tp3,
            catalyst=report.catalyst,
            smart_money=sm_metrics.summary_text,
        )

    return report


# ===========================================================================
# 🏛️ PART 2: INSTITUTIONAL FOREX STRATEGY ENGINE (ICT, SESSIONS, FVG)
# ===========================================================================

FOREX_SYMBOLS = {
    # Forex Majors (/f1)
    "EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD",
    # Forex Crosses & Commodities (/f2)
    "NZD_USD", "EUR_GBP", "EUR_JPY", "GBP_JPY", "XAU_USD", "US30",
}


def _forex_session_filter(data: pd.DataFrame, symbol: str) -> tuple[bool, str]:
    """
    Evaluates institutional trading session windows.
    Returns (is_active_session, session_name).
    """
    if "time" not in data.columns:
        return True, "Standard Session"

    last_time = data.iloc[-1]["time"]
    hour = last_time.hour if hasattr(last_time, "hour") else 12
    is_asian_pair = symbol in {"USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_USD", "NZD_USD"}

    # London Open & Overlap: 07:00 - 16:30 UTC (Peak Institutional Volume)
    if 7 <= hour <= 16:
        return True, "London / NY Session (Peak Volume)"
    
    # New York Afternoon: 16:30 - 20:00 UTC
    if 16 < hour <= 20:
        return True, "New York Afternoon Session"

    # Tokyo / Asian Session: 00:00 - 07:00 UTC
    if 0 <= hour < 7:
        if is_asian_pair:
            return True, "Tokyo / Asian Session"
        return False, "Low Liquidity Asian Night (Waiting for London 07:00 UTC)"

    return False, "Off-Hours Market Roll (Low Liquidity)"


def _forex_ict_liquidity_sweep(data: pd.DataFrame, symbol: str) -> StrategySignal:
    """
    Identifies institutional stop hunts / liquidity raids above Asian High or below Asian Low,
    followed by a Market Structure Shift (MSS) displacement candle back inside the range.
    """
    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    open_p = float(last["open"])

    ref = data.iloc[-40:-1]
    swing_high = float(ref["high"].max())
    swing_low = float(ref["low"].min())

    # Bullish ICT Sweep: Swept below swing low (Sell-Side Liquidity), rejected, and closed with strong bullish body
    swept_low = float(last["low"]) < swing_low and close > swing_low
    is_bullish_displacement = close > open_p and (close - open_p) >= 0.35 * (last["high"] - last["low"])
    has_buy_rejection = check_rejection(last, "buy") or (last["low"] < prev["low"] and close > prev["high"])

    if swept_low and (is_bullish_displacement or has_buy_rejection) and last["macd_hist"] >= prev["macd_hist"]:
        return StrategySignal(
            "ICT Liquidity Sweep (MSS)",
            "BUY",
            92,
            f"Institutional liquidity raid below swing low ({swing_low:.4f}) with Market Structure Shift back into range",
        )

    # Bearish ICT Sweep: Swept above swing high (Buy-Side Liquidity), rejected, and closed with strong bearish body
    swept_high = float(last["high"]) > swing_high and close < swing_high
    is_bearish_displacement = close < open_p and (open_p - close) >= 0.35 * (last["high"] - last["low"])
    has_sell_rejection = check_rejection(last, "sell") or (last["high"] > prev["high"] and close < prev["low"])

    if swept_high and (is_bearish_displacement or has_sell_rejection) and last["macd_hist"] <= prev["macd_hist"]:
        return StrategySignal(
            "ICT Liquidity Sweep (MSS)",
            "SELL",
            92,
            f"Institutional liquidity raid above swing high ({swing_high:.4f}) with Market Structure Shift back into range",
        )

    return StrategySignal("ICT Liquidity Sweep (MSS)", "HOLD", 0, "")


def _forex_fvg_retest(data: pd.DataFrame, symbol: str) -> StrategySignal:
    """
    Detects a 3-candle Fair Value Gap (FVG) / Order Block in the direction of the 200/50 EMA trend
    and triggers when price pulls into the imbalance zone for a discounted entry.
    """
    if len(data) < 10:
        return StrategySignal("Fair Value Gap (FVG)", "HOLD", 0, "")

    last = data.iloc[-1]
    c1 = data.iloc[-4]
    c2 = data.iloc[-3]
    c3 = data.iloc[-2]
    close = float(last["close"])

    ema50 = float(last["ema50"])
    ema200 = float(last["ema200"])
    adx_val = float(last["adx"]) if pd.notna(last["adx"]) else 20

    # Bullish FVG: Candle 1 High < Candle 3 Low (Gap between C1 High and C3 Low)
    is_bullish_fvg = c3["low"] > c1["high"] and c2["close"] > c2["open"]
    if is_bullish_fvg and close > ema50 and ema50 > ema200 and adx_val >= 18:
        fvg_top = float(c3["low"])
        fvg_bottom = float(c1["high"])
        if fvg_bottom <= float(last["low"]) <= fvg_top * 1.002 and close >= fvg_bottom:
            return StrategySignal(
                "Fair Value Gap (FVG)",
                "BUY",
                88,
                f"Bullish FVG imbalance retest ({fvg_bottom:.4f} - {fvg_top:.4f}) in institutional 200 EMA uptrend",
            )

    # Bearish FVG: Candle 1 Low > Candle 3 High (Gap between C1 Low and C3 High)
    is_bearish_fvg = c3["high"] < c1["low"] and c2["close"] < c2["open"]
    if is_bearish_fvg and close < ema50 and ema50 < ema200 and adx_val >= 18:
        fvg_top = float(c1["low"])
        fvg_bottom = float(c3["high"])
        if fvg_bottom * 0.998 <= float(last["high"]) <= fvg_top and close <= fvg_top:
            return StrategySignal(
                "Fair Value Gap (FVG)",
                "SELL",
                88,
                f"Bearish FVG imbalance retest ({fvg_bottom:.4f} - {fvg_top:.4f}) in institutional 200 EMA downtrend",
            )

    return StrategySignal("Fair Value Gap (FVG)", "HOLD", 0, "")


def _forex_london_ny_displacement(data: pd.DataFrame, session_key: str | None, symbol: str) -> StrategySignal:
    """
    Captures high-volume institutional breakout displacement during London/NY sessions.
    """
    if "time" not in data.columns:
        return StrategySignal("London/NY Displacement", "HOLD", 0, "")

    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    atr_val = float(last["atr"]) if pd.notna(last["atr"]) else 0.0010
    adx_val = float(last["adx"]) if pd.notna(last["adx"]) else 0

    if adx_val < 20:
        return StrategySignal("London/NY Displacement", "HOLD", 0, "")

    mask = (
        (data["time"].dt.date == last["time"].date())
        & (data["time"].dt.hour >= 0) & (data["time"].dt.hour < 7)
    )
    asian = data.loc[mask]
    if len(asian) < 4:
        return StrategySignal("London/NY Displacement", "HOLD", 0, "")

    a_high = float(asian["high"].max())
    a_low = float(asian["low"].min())
    candle_size = float(last["high"] - last["low"])

    # Displacements outside Asian range must happen during London or NY (hour >= 7)
    last_hour = last["time"].hour if hasattr(last["time"], "hour") else 8
    if last_hour < 7:
        return StrategySignal("London/NY Displacement", "HOLD", 0, "")

    if close > a_high and prev["close"] <= a_high and candle_size >= 0.9 * atr_val and close > last["open"]:
        return StrategySignal(
            "London/NY Displacement",
            "BUY",
            86,
            f"Institutional displacement break above Asian High ({a_high:.4f}) with volume expansion",
        )
    if close < a_low and prev["close"] >= a_low and candle_size >= 0.9 * atr_val and close < last["open"]:
        return StrategySignal(
            "London/NY Displacement",
            "SELL",
            86,
            f"Institutional displacement break below Asian Low ({a_low:.4f}) with volume expansion",
        )

    return StrategySignal("London/NY Displacement", "HOLD", 0, "")


def _forex_htf_trend_pullback(data: pd.DataFrame, symbol: str) -> StrategySignal:
    """
    Multi-timeframe trend pullback to 50 EMA with 200 EMA directional bias.
    """
    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    ema50 = float(last["ema50"])
    ema200 = float(last["ema200"])
    rsi_val = float(last["rsi"]) if pd.notna(last["rsi"]) else 50
    adx_val = float(last["adx"]) if pd.notna(last["adx"]) else 20

    if adx_val < 18:
        return StrategySignal("Forex Trend Pullback", "HOLD", 0, "")

    bull_trend = ema50 > ema200 and close > ema200
    if bull_trend and prev["low"] <= ema50 * 1.002 and close >= ema50 and (check_rejection(last, "buy") or close > last["open"]) and 45 <= rsi_val <= 68:
        return StrategySignal(
            "Forex Trend Pullback",
            "BUY",
            84,
            f"Dynamic 50 EMA bounce in 200 EMA macro uptrend (RSI {rsi_val:.0f}, ADX {adx_val:.0f})",
        )

    bear_trend = ema50 < ema200 and close < ema200
    if bear_trend and prev["high"] >= ema50 * 0.998 and close <= ema50 and (check_rejection(last, "sell") or close < last["open"]) and 32 <= rsi_val <= 55:
        return StrategySignal(
            "Forex Trend Pullback",
            "SELL",
            84,
            f"Dynamic 50 EMA rejection in 200 EMA macro downtrend (RSI {rsi_val:.0f}, ADX {adx_val:.0f})",
        )

    return StrategySignal("Forex Trend Pullback", "HOLD", 0, "")


def _forex_structural_sl_tp(
    symbol: str,
    action: str,
    close: float,
    atr_val: float,
    data: pd.DataFrame,
    entry_price: float | None = None,
) -> tuple[float, float, float, float]:
    """
    Computes institutional structural SL (behind swing low/high + buffer)
    and multi-tier Take Profit targets (TP1 1:1.0, TP2 1:2.0, TP3 1:3.5).
    Enforces maximum 2.2% leverage distance cap for 25x survival.
    If entry_price is provided (e.g. for Limit Orders), risk and TP targets
    are anchored to entry_price rather than close.
    """
    if symbol in ("XAU_USD", "US30"):
        min_buffer = atr_val * 2.0 if atr_val > 0 else 3.5
    elif "JPY" in symbol:
        min_buffer = max(atr_val * 1.8, 0.25)
    else:
        min_buffer = max(atr_val * 1.8, 0.0018)

    max_risk_dist = close * 0.022  # Max 2.2% price risk for 25x leverage
    ref_entry = entry_price if entry_price is not None else close

    if action == "BUY":
        if len(data) >= 15:
            swing_low = float(data.iloc[-25:-1]["low"].min())
            sl = min(close - min_buffer, swing_low - atr_val * 0.4)
        else:
            sl = close - min_buffer
        # 25x leverage risk clamp
        sl = max(sl, close - max_risk_dist)
        risk = max(ref_entry - sl, min_buffer)
        tp1 = ref_entry + risk * 1.0  # Pure 1:1 TP1
        tp2 = ref_entry + risk * 2.0
        tp3 = ref_entry + risk * 3.5
    elif action == "SELL":
        if len(data) >= 15:
            swing_high = float(data.iloc[-25:-1]["high"].max())
            sl = max(close + min_buffer, swing_high + atr_val * 0.4)
        else:
            sl = close + min_buffer
        # 25x leverage risk clamp
        sl = min(sl, close + max_risk_dist)
        risk = max(sl - ref_entry, min_buffer)
        tp1 = ref_entry - risk * 1.0  # Pure 1:1 TP1
        tp2 = ref_entry - risk * 2.0
        tp3 = ref_entry - risk * 3.5
    else:
        sl = tp1 = tp2 = tp3 = 0.0

    return sl, tp1, tp2, tp3


def _build_forex_report(
    symbol: str,
    action: str,
    confidence: int,
    trend: str,
    close: float,
    atr_val: float,
    reason: str,
    data: pd.DataFrame,
    session_name: str,
) -> SignalReport:
    sl, tp1, tp2, tp3 = _forex_structural_sl_tp(symbol, action, close, atr_val, data)
    if USE_LIMIT_ORDERS:
        if symbol in ("XAU_USD", "US30"):
            pb = max(atr_val * 0.25, 0.5)
        elif "JPY" in symbol:
            pb = max(atr_val * 0.25, 0.04)
        else:
            pb = max(atr_val * 0.25, 0.0003)

        if action == "BUY":
            limit_entry = close - pb
            if limit_entry <= sl:
                limit_entry = close - (close - sl) * 0.3
            entry_val = limit_entry
            order_type = "LIMIT"
        elif action == "SELL":
            limit_entry = close + pb
            if limit_entry >= sl:
                limit_entry = close + (sl - close) * 0.3
            entry_val = limit_entry
            order_type = "LIMIT"
        else:
            entry_val = close
            order_type = "MARKET"

        # Re-anchor targets to limit entry for pure 1:1 R:R
        sl, tp1, tp2, tp3 = _forex_structural_sl_tp(symbol, action, close, atr_val, data, entry_price=entry_val)
    else:
        entry_val = close
        order_type = "MARKET"

    return SignalReport(
        symbol=DISPLAY_NAMES.get(symbol, symbol),
        action=action,
        confidence=confidence,
        trend=trend,
        entry=entry_val,
        stop_loss=sl,
        take_profit=tp2,
        reason=f"{reason} [{session_name}]",
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        order_type=order_type,
        market_price=close,
    )


def _analyze_forex_setup(
    symbol: str,
    data: pd.DataFrame,
    bias: str,
    min_confidence: int = 65,
    session_key: str | None = None,
) -> SignalReport:
    """
    Dedicated Institutional Forex & Commodities Engine.
    Uses ICT Liquidity Sweeps, Fair Value Gaps, London/NY Displacement, and Structural SL.
    """
    last = data.iloc[-1]
    close = float(last["close"])
    atr_val = float(last["atr"]) if pd.notna(last["atr"]) else 0.0010
    trend = "Bullish" if bias == "bullish" else ("Bearish" if bias == "bearish" else "Ranging")

    is_session_active, session_name = _forex_session_filter(data, symbol)

    # Front-gate off-hours sessions: Spreads widen drastically and institutional liquidity is absent
    if not is_session_active:
        return SignalReport(
            symbol=DISPLAY_NAMES.get(symbol, symbol),
            action="WAIT",
            confidence=40,
            trend=trend,
            entry=None,
            stop_loss=None,
            take_profit=None,
            reason=f"Off-hours: {session_name}. Institutional volume lowest and broker spreads widen.",
        )

    strategies = [
        _forex_ict_liquidity_sweep(data, symbol),
        _forex_fvg_retest(data, symbol),
        _forex_london_ny_displacement(data, session_key, symbol),
        _forex_htf_trend_pullback(data, symbol),
    ]

    buys = [s for s in strategies if s.action == "BUY"]
    sells = [s for s in strategies if s.action == "SELL"]

    best_buy = max(buys, key=lambda s: s.confidence) if buys else None
    best_sell = max(sells, key=lambda s: s.confidence) if sells else None

    gate_threshold = min_confidence if (min_confidence is not None and min_confidence > 0) else 65

    # Prioritize direction with highest confidence!
    candidates = []
    if best_buy is not None:
        candidates.append((best_buy, "BUY", buys))
    if best_sell is not None:
        candidates.append((best_sell, "SELL", sells))
    candidates.sort(key=lambda item: item[0].confidence, reverse=True)

    for cand, direction, same in candidates:
        unique_strategies = {s.name for s in same}
        if len(unique_strategies) >= 2:
            base_conf = max(s.confidence for s in same)
            confluence_bonus = min(4 * (len(unique_strategies) - 1), 8)
            compounded_conf = min(98, base_conf + confluence_bonus)
            if compounded_conf >= gate_threshold:
                reason = " | ".join(s.reason for s in same)
                return _build_forex_report(
                    symbol, direction, compounded_conf, trend, close, atr_val, reason, data, session_name
                )

        if cand.confidence >= gate_threshold:
            return _build_forex_report(
                symbol, direction, cand.confidence, trend, close, atr_val, cand.reason, data, session_name
            )

    # Fallback to multi-factor scoring during active session
    fb_action, fb_conf, fb_reason, long_s, short_s = _fallback_scoring(data)
    if fb_action != "WAIT" and fb_conf >= gate_threshold:
        return _build_forex_report(
            symbol, fb_action, fb_conf, trend, close, atr_val, fb_reason, data, session_name
        )

    fallback_conf = max(long_s, short_s)
    return SignalReport(
        symbol=DISPLAY_NAMES.get(symbol, symbol),
        action="WAIT",
        confidence=fallback_conf,
        trend=trend,
        entry=None,
        stop_loss=None,
        take_profit=None,
        reason=f"No institutional sweep/FVG confluence ({session_name})",
    )


# ===========================================================================
# 🌐 PUBLIC ENTRY POINT (MARKET ROUTER)
# ===========================================================================

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

    # Route 1: Crypto & Memecoins (100% Isolated & Untouched)
    if symbol in CRYPTO_SYMBOLS:
        return _analyze_crypto_setup(symbol, data, bias, min_confidence=min_confidence)

    # Route 2: Dedicated Institutional Forex Engine (ICT Sweeps, FVG, Sessions)
    return _analyze_forex_setup(symbol, data, bias, min_confidence=min_confidence, session_key=session_key)
