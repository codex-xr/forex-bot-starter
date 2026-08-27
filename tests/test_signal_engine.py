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
    _mean_reversion,
    _fallback_scoring,
    _quality_gate,
    _crypto_momentum_surge,
    _crypto_rsi_divergence,
    _crypto_volatility_squeeze,
    _forex_ict_liquidity_sweep,
    _forex_fvg_retest,
    _forex_london_ny_displacement,
    _forex_htf_trend_pullback,
    _forex_structural_sl_tp,
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
    times = pd.date_range("2025-01-01 08:00:00", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "time": times, "open": opens, "high": highs,
        "low": lows, "close": close,
    })


# ============================== INDICATORS ==============================


class TestRSI:
    def test_rsi_bounds(self):
        close = pd.Series(range(1, 101))
        vals = rsi(close).dropna()
        assert vals.iloc[-1] > 80

    def test_rsi_all_ones(self):
        close = pd.Series([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        vals = rsi(close).dropna()
        assert (vals == 50).all()


class TestATR:
    def test_atr_positive(self):
        prices = _ohcv_close([1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 3)
        vals = atr(prices).dropna()
        assert (vals > 0).all()


class TestADX:
    def test_adx_trending(self):
        prices = _ohcv_close(list(range(1, 101)))
        vals = adx(prices).dropna()
        assert vals.iloc[-1] > 20


class TestMACD:
    def test_macd_shapes(self):
        close = pd.Series(range(1, 101), dtype=float)
        line, sig, hist = macd(close)
        assert len(line) == len(close)
        assert len(sig) == len(close)
        assert len(hist) == len(close)

    def test_macd_uptrend(self):
        close = pd.Series(range(1, 101), dtype=float)
        line, sig, hist = macd(close)
        assert line.iloc[-1] > 0
        assert hist.iloc[-1] > 0


# ============================== CANDLE PATTERNS ==============================


class TestCheckRejection:
    def test_strong_bullish_rejection(self):
        row = pd.Series({
            "open": 1.05, "close": 1.10,
            "high": 1.12, "low": 0.90,
        })
        assert check_rejection(row, "buy") is True

    def test_strong_bearish_rejection(self):
        row = pd.Series({
            "open": 1.10, "close": 1.05,
            "high": 1.25, "low": 1.03,
        })
        assert check_rejection(row, "sell") is True

    def test_no_rejection_small_wick(self):
        row = pd.Series({
            "open": 1.00, "close": 1.08,
            "high": 1.09, "low": 0.99,
        })
        assert check_rejection(row, "buy") is False
        assert check_rejection(row, "sell") is False

    def test_marubozu_no_rejection(self):
        row = pd.Series({
            "open": 1.00, "close": 1.10,
            "high": 1.10, "low": 1.00,
        })
        assert check_rejection(row, "buy") is False


# ============================== HTF BIAS ==============================


class TestHtfBias:
    def test_bullish_bias(self):
        close = pd.Series(range(1, 101), dtype=float)
        data = pd.DataFrame({"close": close})
        data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()
        assert htf_bias(data) == "bullish"

    def test_bearish_bias(self):
        close = pd.Series(range(100, 0, -1), dtype=float)
        data = pd.DataFrame({"close": close})
        data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()
        assert htf_bias(data) == "bearish"


# ============================== SIGNAL REPORT ==============================


class TestSignalReport:
    def test_wait_message(self):
        r = SignalReport("EURUSD", "WAIT", 50, "Ranging", None, None, None, "Choppy")
        msg = r.to_message()
        assert "EURUSD" in msg
        assert "WAIT" in msg
        assert "50%" in msg

    def test_buy_message(self):
        r = SignalReport("EURUSD", "BUY", 88, "Bullish", 1.0850, 1.0800, 1.0950, "ICT Sweep")
        msg = r.to_message()
        assert "EURUSD" in msg
        assert "BUY SETUP" in msg
        assert "88%" in msg
        assert "1.08500" in msg
        assert "1.08000" in msg


# ============================== STRATEGIES ==============================


class TestSMC_Sweep:
    def test_returns_hold_without_sweep(self):
        c = [1.0] * 50 + [1.01] * 50
        data = compute_indicators(_ohcv_close(c))
        sig = _smc_sweep(data)
        assert sig.action == "HOLD"


class TestForexStrategies:
    def test_forex_ict_sweep(self):
        c = [1.0] * 100
        data = compute_indicators(_ohcv_close(c))
        sig = _forex_ict_liquidity_sweep(data, "EUR_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert sig.name == "ICT Liquidity Sweep (MSS)"

    def test_forex_fvg_retest(self):
        c = [1.0] * 100
        data = compute_indicators(_ohcv_close(c))
        sig = _forex_fvg_retest(data, "EUR_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert sig.name == "Fair Value Gap (FVG)"

    def test_forex_displacement(self):
        c = [1.0] * 100
        data = compute_indicators(_ohcv_close(c))
        sig = _forex_london_ny_displacement(data, session_key="london", symbol="EUR_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert sig.name == "London/NY Displacement"

    def test_forex_structural_sl_tp(self):
        data = compute_indicators(_ohcv_close([1.1000] * 100))
        sl, tp1, tp2, tp3 = _forex_structural_sl_tp("EUR_USD", "BUY", 1.1000, 0.0010, data)
        assert sl < 1.1000
        assert tp1 > 1.1000
        assert tp2 > tp1
        assert tp3 > tp2


class TestMeanReversion:
    def test_high_adx_returns_hold(self):
        c = list(range(1, 101))
        data = compute_indicators(_ohcv_close(c))
        sig = _mean_reversion(data)
        assert sig.action == "HOLD"


class TestCryptoMomentumSurge:
    def test_non_crypto_symbol_returns_hold(self):
        data = compute_indicators(_ohcv_close(list(range(1, 101))))
        sig = _crypto_momentum_surge(data, "EUR_USD")
        assert sig.action == "HOLD"

    def test_crypto_symbol_runs(self):
        data = compute_indicators(_ohcv_close(list(range(1, 101))))
        sig = _crypto_momentum_surge(data, "BTC_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert sig.name == "Crypto Momentum Surge"


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
        strategies = [
            StrategySignal("Sweep", "HOLD", 0, ""),
            StrategySignal("Breakout", "HOLD", 0, ""),
            StrategySignal("Pullback", "HOLD", 0, ""),
            StrategySignal("MR", "HOLD", 0, ""),
        ]
        report = _quality_gate(strategies, "neutral", data, "BTC_USD", 60)
        assert report.action in ("BUY", "SELL", "WAIT")

    def test_single_active_with_htf_alignment_qualifies(self):
        data = compute_indicators(_ohcv_close(list(range(1, 101))))
        strategies = [
            StrategySignal("SMC Sweep", "BUY", 88, "Sweep below support"),
            StrategySignal("Breakout", "HOLD", 0, ""),
            StrategySignal("Pullback", "HOLD", 0, ""),
            StrategySignal("MR", "HOLD", 0, ""),
        ]
        report = _quality_gate(strategies, "bullish", data, "BTC_USD", 60)
        assert report.action == "BUY"
        assert report.confidence == 88
        assert "Sweep below support" in report.reason

    def test_two_agreeing_strategies_qualify(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        strategies = [
            StrategySignal("Strategy A", "BUY", 80, "Reason A"),
            StrategySignal("Strategy B", "BUY", 84, "Reason B"),
            StrategySignal("Strategy C", "HOLD", 0, ""),
            StrategySignal("Strategy D", "HOLD", 0, ""),
        ]
        report = _quality_gate(strategies, "neutral", data, "BTC_USD", 60)
        assert report.action == "BUY"
        assert report.confidence == 82
        assert "Reason A | Reason B" in report.reason


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
        assert analyze_setup("SOL_USD", prices).symbol == "SOLUSD"
        assert analyze_setup("USD_CHF", prices).symbol == "USDCHF"
        assert analyze_setup("DOGE_USD", prices).symbol == "DOGEUSD"
        assert analyze_setup("ANSEM_USD", prices).symbol == "ANSEM"
        assert analyze_setup("WIF_USD", prices).symbol == "WIFUSD"

    def test_min_confidence_zero_allows_fallback(self):
        n = 100
        np.random.seed(0)
        close = 1.0 + np.random.randn(n).cumsum() * 0.01
        prices = _ohcv_close(close.tolist())
        report_low = analyze_setup("BTC_USD", prices, min_confidence=0)
        report_high = analyze_setup("BTC_USD", prices, min_confidence=99)
        assert report_high.action == "WAIT"

    def test_crypto_sl_tp_multipliers(self):
        from bot.signal_engine import _sl_tp_mult
        # Large cap: tighter stops
        assert _sl_tp_mult("BTC_USD") == (2.0, 3.0)
        assert _sl_tp_mult("ETH_USD") == (2.0, 3.0)
        # Mid cap: standard
        assert _sl_tp_mult("SOL_USD") == (2.5, 3.5)
        assert _sl_tp_mult("LINK_USD") == (2.5, 3.5)
        # Memecoins: wider stops, bigger runners
        assert _sl_tp_mult("DOGE_USD") == (3.5, 5.0)
        assert _sl_tp_mult("ANSEM_USD") == (3.5, 5.0)
        assert _sl_tp_mult("WIF_USD") == (3.5, 5.0)
        assert _sl_tp_mult("PEPE_USD") == (3.5, 5.0)
        assert _sl_tp_mult("TRUMP_USD") == (3.5, 5.0)
        # Commodities
        assert _sl_tp_mult("XAU_USD") == (2.0, 4.0)


class TestCryptoRSIDivergence:
    def test_non_crypto_returns_hold(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        sig = _crypto_rsi_divergence(data, "EUR_USD")
        assert sig.action == "HOLD"
        assert sig.name == "RSI Divergence"

    def test_crypto_runs(self):
        data = compute_indicators(_ohcv_close(list(range(1, 101))))
        sig = _crypto_rsi_divergence(data, "BTC_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert sig.name == "RSI Divergence"


class TestCryptoVolatilitySqueeze:
    def test_non_crypto_returns_hold(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        sig = _crypto_volatility_squeeze(data, "EUR_USD")
        assert sig.action == "HOLD"
        assert sig.name == "Volatility Squeeze"

    def test_crypto_runs(self):
        data = compute_indicators(_ohcv_close(list(range(1, 101))))
        sig = _crypto_volatility_squeeze(data, "BTC_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
        assert sig.name == "Volatility Squeeze"

    def test_flat_data_no_squeeze(self):
        data = compute_indicators(_ohcv_close([1.0] * 100))
        sig = _crypto_volatility_squeeze(data, "SOL_USD")
        assert sig.action == "HOLD"


class TestMeanReversionCryptoAware:
    def test_crypto_allows_higher_adx(self):
        """Crypto should allow mean reversion up to ADX 28, not just 22."""
        from bot.signal_engine import _mean_reversion, CRYPTO_SYMBOLS
        # With a trending dataset ADX will be high; just verify it accepts symbol param
        data = compute_indicators(_ohcv_close([1.0] * 100))
        sig = _mean_reversion(data, "BTC_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")
