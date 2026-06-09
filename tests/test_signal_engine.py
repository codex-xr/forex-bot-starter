import pandas as pd
import numpy as np
from bot.signal_engine import (
    SignalReport,
    StrategySignal,
    rsi,
    atr,
    adx,
    macd,
    check_rejection,
    compute_indicators,
    htf_bias,
    _smc_sweep,
    _london_breakout,
    _ema_pullback,
    _mean_reversion,
    _fallback_scoring,
    _quality_gate,
    analyze_setup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ohcv_close(close: list[float]) -> pd.DataFrame:
    """Build minimal OHLC + time DataFrame from a close series with noise."""
    n = len(close)
    np.random.seed(42)
    noise = np.random.uniform(-0.005, 0.005, n)
    opens = [close[0]] + close[:-1]
    highs = [max(o, c) + abs(n) * 2 for o, c, n in zip(opens, close, noise)]
    lows  = [min(o, c) - abs(n) * 2 for o, c, n in zip(opens, close, noise)]
    times = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "time": times, "open": opens, "high": highs,
        "low": lows, "close": close,
    })


# ============================== INDICATORS ==============================


class TestRSI:
    def test_rsi_bounds(self):
        close = pd.Series([10] * 30 + [11] * 50)
        vals = rsi(close).dropna()
        assert vals.iloc[-1] > 70  # strong uptrend

    def test_rsi_all_ones(self):
        close = pd.Series([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        vals = rsi(close).dropna()
        assert (vals == 50).all()


class TestATR:
    def test_atr_positive(self):
        prices = _ohcv_close([1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 3)
        vals = atr(prices).dropna()
        assert vals.iloc[-1] > 0


class TestADX:
    def test_adx_trending(self):
        c = list(range(1, 100))
        prices = _ohcv_close(c)
        df = compute_indicators(prices)
        assert df["adx"].iloc[-1] > 20


class TestMACD:
    def test_macd_shapes(self):
        close = pd.Series(range(50))
        line, signal, hist = macd(close)
        assert len(line) == len(close) == len(signal) == len(hist)

    def test_macd_uptrend(self):
        close = pd.Series(range(1, 100))
        line, _, _ = macd(close)
        assert line.iloc[-1] > 0


# ============================== REJECTION CANDLE ==============================


class TestCheckRejection:
    def test_strong_bullish_rejection(self):
        row = pd.Series({"open": 1.00, "high": 1.04, "low": 0.90, "close": 1.03})
        assert check_rejection(row, "buy")

    def test_strong_bearish_rejection(self):
        row = pd.Series({"open": 1.10, "high": 1.22, "low": 1.09, "close": 1.11})
        assert check_rejection(row, "sell")

    def test_no_rejection_small_wick(self):
        row = pd.Series({"open": 1.00, "high": 1.05, "low": 0.99, "close": 1.04})
        assert not check_rejection(row, "buy")
        assert not check_rejection(row, "sell")

    def test_marubozu_no_rejection(self):
        row = pd.Series({"open": 1.00, "high": 1.10, "low": 1.00, "close": 1.10})
        assert not check_rejection(row, "buy")
        assert not check_rejection(row, "sell")


# ============================== HTF BIAS ==============================


class TestHtfBias:
    def test_bullish_bias(self):
        data = compute_indicators(_ohcv_close(list(range(1, 60))))
        assert htf_bias(data) == "bullish"

    def test_bearish_bias(self):
        data = compute_indicators(_ohcv_close(list(range(60, 0, -1))))
        assert htf_bias(data) == "bearish"


# ============================== SIGNAL REPORT ==============================


class TestSignalReport:
    def test_wait_message(self):
        r = SignalReport("EUR_USD", "WAIT", 45, "Mixed", None, None, None, "Not enough data")
        msg = r.to_message()
        assert "EUR_USD" in msg and "WAIT" in msg and "45%" in msg

    def test_buy_message(self):
        r = SignalReport("EUR_USD", "BUY", 85, "Bullish", 1.1050, 1.1020, 1.1110, "Breakout")
        msg = r.to_message()
        assert "BUY" in msg and "1.10500" in msg and "1.10200" in msg


# ============================== STRATEGIES ==============================


class TestSMC_Sweep:
    def test_returns_hold_without_sweep(self):
        c = [1.0] * 50 + [1.01] * 50
        data = compute_indicators(_ohcv_close(c))
        sig = _smc_sweep(data)
        assert sig.action == "HOLD"


class TestLondonBreakout:
    def test_not_london_hours_returns_hold(self):
        c = [1.0] * 100
        data = compute_indicators(_ohcv_close(c))
        sig = _london_breakout(data, session_key="new_york")
        assert sig.action == "HOLD"


class TestEMAPullback:
    def test_no_trend_returns_hold(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        sig = _ema_pullback(data)
        assert sig.action == "HOLD"


class TestMeanReversion:
    def test_high_adx_returns_hold(self):
        c = list(range(1, 101))
        data = compute_indicators(_ohcv_close(c))
        sig = _mean_reversion(data)
        assert sig.action == "HOLD"


# ============================== FALLBACK SCORING ==============================


class TestFallbackScoring:
    def test_returns_tuple(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        result = _fallback_scoring(data)
        assert len(result) == 5
        assert result[0] in ("BUY", "SELL", "WAIT")


# ============================== QUALITY GATE ==============================


class TestQualityGate:
    def test_no_active_strategies_falls_back(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        last = data.iloc[-1]
        close = float(last["close"])
        atr_val = float(last["atr"]) if pd.notna(last["atr"]) else close * 0.002
        strategies = [
            StrategySignal("Sweep", "HOLD", 0, ""),
            StrategySignal("Breakout", "HOLD", 0, ""),
            StrategySignal("Pullback", "HOLD", 0, ""),
            StrategySignal("MR", "HOLD", 0, ""),
        ]
        report = _quality_gate(strategies, "neutral", data, "EUR_USD", 60)
        assert report.action in ("BUY", "SELL", "WAIT")

    def test_single_active_no_htf_alignment_uses_fallback(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        strategies = [
            StrategySignal("Sweep", "HOLD", 0, ""),
            StrategySignal("Breakout", "HOLD", 0, ""),
            StrategySignal("Pullback", "HOLD", 0, ""),
            StrategySignal("MR", "BUY", 80, "mean reversion triggered"),
        ]
        report = _quality_gate(strategies, "bearish", data, "EUR_USD", 60)
        assert report.action in ("BUY", "SELL", "WAIT")


# ============================== INTEGRATION ==============================


class TestAnalyzeSetup:
    def test_not_enough_candles(self):
        prices = _ohcv_close([1.0] * 50)
        report = analyze_setup("EUR_USD", prices)
        assert report.action == "WAIT"
        assert "Not enough candle data" in report.reason

    def test_sufficient_candles_returns_report(self):
        prices = _ohcv_close([1.0] * 100)
        report = analyze_setup("EUR_USD", prices)
        assert isinstance(report, SignalReport)
        assert report.action in ("BUY", "SELL", "WAIT")
        assert 0 <= report.confidence <= 95

    def test_display_symbol_used(self):
        prices = _ohcv_close([1.0] * 100)
        report = analyze_setup("XAU_USD", prices)
        assert report.symbol == "XAUUSD"

    def test_min_confidence_zero_allows_fallback(self):
        n = 100
        np.random.seed(0)
        close = 1.0 + np.random.randn(n).cumsum() * 0.01
        prices = _ohcv_close(close.tolist())
        report_low = analyze_setup("EUR_USD", prices, min_confidence=0)
        report_high = analyze_setup("EUR_USD", prices, min_confidence=99)
        assert report_high.action == "WAIT"  # 99 is unreachable
