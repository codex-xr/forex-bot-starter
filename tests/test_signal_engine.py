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
        # Compounded confidence: 84 + 4 = 88 (not diluted to 82)
        assert report.confidence == 88
        assert "Reason A | Reason B" in report.reason

    def test_highest_conviction_direction_wins(self):
        """Ensure SELL with 92% confidence wins over BUY with 78% confidence (no hardcoded BUY bias)."""
        data = compute_indicators(_ohcv_close([1.0] * 100))
        strategies = [
            StrategySignal("Weak Buy", "BUY", 78, "Weak bounce"),
            StrategySignal("Strong Sell", "SELL", 92, "Institutional breakdown"),
        ]
        report = _quality_gate(strategies, "neutral", data, "BTC_USD", 60)
        assert report.action == "SELL"
        assert report.confidence == 92
        assert "Institutional breakdown" in report.reason


# ============================== INTEGRATION ==============================


class TestAnalyzeSetup:
    def test_not_enough_candles(self):
        prices = _ohcv_close([1.0] * 50)
        report = analyze_setup("BTC_USD", prices)
        assert report.action == "WAIT"
        assert "Not enough candle data" in report.reason

    def test_sufficient_candles_returns_report(self):
        prices = _ohcv_close([1.0] * 100)
        report = analyze_setup("BTC_USD", prices)
        assert isinstance(report, SignalReport)
        assert report.action in ("BUY", "SELL", "WAIT")
        assert 0 <= report.confidence <= 95

    def test_display_symbol_used(self):
        prices = _ohcv_close([1.0] * 100)
        assert analyze_setup("BTC_USD", prices).symbol == "BTCUSD"
        assert analyze_setup("SOL_USD", prices).symbol == "SOLUSD"
        assert analyze_setup("ETH_USD", prices).symbol == "ETHUSD"
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


class TestMeanReversionCryptoAware:
    def test_crypto_allows_higher_adx(self):
        """Crypto should allow mean reversion up to ADX 28, not just 22."""
        from bot.signal_engine import _mean_reversion, CRYPTO_SYMBOLS
        data = compute_indicators(_ohcv_close([1.0] * 100))
        sig = _mean_reversion(data, "BTC_USD")
        assert sig.action in ("BUY", "SELL", "HOLD")


class TestSmartMoneyVeto:
    def test_buy_vetoed_when_whales_are_short(self):
        from bot.binance_engine import BinanceSmartMoneyMetrics
        metrics = BinanceSmartMoneyMetrics(
            symbol="BTC_USD",
            top_trader_long_pct=35.0,  # 65% short
            top_trader_ls_ratio=0.54,
        )
        is_vetoed, reason = metrics.is_vetoed("BUY")
        assert is_vetoed is True
        assert "Top Trader Whales are heavily short" in reason

    def test_buy_vetoed_when_funding_overheated(self):
        from bot.binance_engine import BinanceSmartMoneyMetrics
        metrics = BinanceSmartMoneyMetrics(
            symbol="BTC_USD",
            funding_rate_pct=0.045,  # Overheated long flush risk
        )
        is_vetoed, reason = metrics.is_vetoed("BUY")
        assert is_vetoed is True
        assert "Funding rate overheated" in reason

    def test_sell_vetoed_when_whales_are_long(self):
        from bot.binance_engine import BinanceSmartMoneyMetrics
        metrics = BinanceSmartMoneyMetrics(
            symbol="ETH_USD",
            top_trader_long_pct=68.0,  # 68% long
            top_trader_ls_ratio=2.12,
        )
        is_vetoed, reason = metrics.is_vetoed("SELL")
        assert is_vetoed is True
        assert "Top Trader Whales are heavily long" in reason

    def test_sell_vetoed_when_funding_negative_squeeze(self):
        from bot.binance_engine import BinanceSmartMoneyMetrics
        metrics = BinanceSmartMoneyMetrics(
            symbol="SOL_USD",
            funding_rate_pct=-0.035,  # Extreme short squeeze risk
        )
        is_vetoed, reason = metrics.is_vetoed("SELL")
        assert is_vetoed is True
        assert "short squeeze" in reason


class TestLeverageStopLossClamp:
    def test_crypto_sl_clamped_for_25x_leverage(self):
        from bot.signal_engine import _make_report
        prices = _ohcv_close([100.0] * 100)
        prices.loc[80, "low"] = 70.0
        data = compute_indicators(prices)
        report = _make_report("BTC_USD", "BUY", 90, "Bullish", 100.0, 0.5, "Breakout", data=data)
        # BTC is large cap: max risk is 2.0% => sl >= 98.0
        assert report.stop_loss is not None
        assert report.stop_loss >= 98.0



class TestLimitOrderSignals:
    def test_crypto_buy_limit_pricing(self, monkeypatch):
        import bot.signal_engine as se
        monkeypatch.setattr(se, "USE_LIMIT_ORDERS", True)
        prices = _ohcv_close([50000.0] * 100)
        data = compute_indicators(prices)
        report = se._make_report("BTC_USD", "BUY", 90, "Bullish", 50000.0, 500.0, "Breakout", data=data)
        assert report.order_type == "LIMIT"
        assert report.entry is not None
        assert report.entry < 50000.0  # Discount pullback
        assert report.stop_loss is not None
        assert report.entry > report.stop_loss
        expected_tp1 = round(report.entry + (report.entry - report.stop_loss) * 1.0, 8)
        assert round(report.tp1, 4) == round(expected_tp1, 4)

    def test_crypto_sell_limit_pricing(self, monkeypatch):
        import bot.signal_engine as se
        monkeypatch.setattr(se, "USE_LIMIT_ORDERS", True)
        prices = _ohcv_close([50000.0] * 100)
        data = compute_indicators(prices)
        report = se._make_report("BTC_USD", "SELL", 90, "Bearish", 50000.0, 500.0, "Breakdown", data=data)
        assert report.order_type == "LIMIT"
        assert report.entry is not None
        assert report.entry > 50000.0  # Premium pullback
        assert report.stop_loss is not None
        assert report.entry < report.stop_loss
        expected_tp1 = round(report.entry - (report.stop_loss - report.entry) * 1.0, 8)
        assert round(report.tp1, 4) == round(expected_tp1, 4)

    def test_market_order_default_and_entry_zone(self):
        import bot.signal_engine as se
        prices = _ohcv_close([50000.0] * 100)
        data = compute_indicators(prices)
        report = se._make_report("BTC_USD", "BUY", 90, "Bullish", 50000.0, 500.0, "Breakout", data=data)
        assert report.order_type == "MARKET"
        assert report.entry == 50000.0
        msg = report.to_message()
        assert "Instant Market <b>BUY NOW</b>" in msg
        assert "📍 <b>Entry Zone:</b>" in msg

    def test_stop_loss_minimum_buffer(self):
        import bot.signal_engine as se
        prices = _ohcv_close([85000.0] * 100)
        data = compute_indicators(prices)
        # Low ATR of 50 on BTC should still have at least 0.8% ($680) risk breathing room
        report = se._make_report("BTC_USD", "BUY", 90, "Bullish", 85000.0, 50.0, "Breakout", data=data)
        sl_dist = report.entry - report.stop_loss
        assert sl_dist >= 85000.0 * 0.008  # at least $680

    def test_limit_order_toggle_reversibility(self, monkeypatch):
        import bot.signal_engine as se
        monkeypatch.setattr(se, "USE_LIMIT_ORDERS", False)
        prices = _ohcv_close([50000.0] * 100)
        data = compute_indicators(prices)
        report = se._make_report("BTC_USD", "BUY", 90, "Bullish", 50000.0, 500.0, "Breakout", data=data)
        assert report.order_type == "MARKET"
        assert report.entry == 50000.0

        monkeypatch.setattr(se, "USE_LIMIT_ORDERS", True)
        report_limit = se._make_report("BTC_USD", "BUY", 90, "Bullish", 50000.0, 500.0, "Breakout", data=data)
        assert report_limit.order_type == "LIMIT"
        assert report_limit.entry < 50000.0

    def test_limit_order_copy_paste_message_format(self):
        from bot.signal_engine import SignalReport
        r = SignalReport(
            symbol="BTCUSD",
            action="BUY",
            confidence=85,
            trend="Bullish",
            entry=49800.0,
            stop_loss=49000.0,
            take_profit=50600.0,
            reason="ICT Sweep + FVG",
            tp1=50600.0,
            tp2=51400.0,
            tp3=52600.0,
            order_type="LIMIT",
            market_price=50000.0,
        )
        msg = r.to_message()
        assert "VIP SIGNAL: BTCUSD" in msg
        assert "ORDER TYPE:</b> <b>BUY LIMIT" in msg
        assert "LIMIT ENTRY:</b> <code>49800.00" in msg
        assert "Current Price:</b> <code>50000.00" in msg
        assert "Cancel if TP1 reached before limit entry" in msg


class TestSignalQualityUpgrades:
    def test_rvol_carries_over_from_closed_candle(self):
        prices = _ohcv_close([100.0] * 30)
        prices["volume"] = [1000.0] * 28 + [2500.0, 100.0]  # prev candle 2.5x RVol, current unclosed candle 0.1x
        data = compute_indicators(prices)
        assert data["rvol"].iloc[-1] >= 2.0

    def test_anti_chasing_overextended_climax_blocked(self):
        # Normal steady uptrend followed by an extreme 10x ATR climax candle
        closes = [100.0 + i * 0.2 for i in range(99)] + [145.0]
        prices = _ohcv_close(closes)
        prices.loc[99, "open"] = 120.0
        prices.loc[99, "high"] = 146.0
        prices.loc[99, "low"] = 119.5
        prices["volume"] = [1000.0] * 99 + [5000.0]
        data = compute_indicators(prices)
        sig = _crypto_momentum_surge(data, "BTC_USD")
        assert sig.action == "HOLD"

    def test_fallback_scoring_blocks_opposite_color_candle(self):
        # Uptrending series where the final candle dumps hard red below open
        closes = [100.0 + i * 0.5 for i in range(99)] + [148.0]
        prices = _ohcv_close(closes)
        prices.loc[99, "open"] = 152.0  # Bearish red candle (close 148 < open 152)
        prices.loc[99, "high"] = 152.5
        prices.loc[99, "low"] = 147.5
        data = compute_indicators(prices)
        action, _, _, _, _ = _fallback_scoring(data)
        assert action != "BUY"


