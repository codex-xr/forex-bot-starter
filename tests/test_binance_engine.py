from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
import pytest

from bot.binance_engine import (
    get_binance_symbol_pair,
    fetch_binance_klines,
    fetch_binance_derivatives,
    analyze_binance_smart_money,
    BinanceSmartMoneyMetrics,
)
from bot.signal_engine import analyze_setup


def _sample_crypto_df(length=100, trend="up"):
    np.random.seed(42)
    dates = pd.date_range("2026-09-01", periods=length, freq="15min", tz="UTC")
    base = 60000.0 if trend == "up" else 70000.0
    step = 50.0 if trend == "up" else -50.0

    closes = [base + i * step + np.random.normal(0, 10) for i in range(length)]
    highs = [c + np.random.uniform(5, 30) for c in closes]
    lows = [c - np.random.uniform(5, 30) for c in closes]
    opens = [closes[max(0, i - 1)] for i in range(length)]
    volumes = [np.random.uniform(100, 500) for _ in range(length)]
    taker_buy = [v * (0.65 if trend == "up" else 0.35) for v in volumes]

    return pd.DataFrame({
        "time": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "taker_buy_base_vol": taker_buy,
        "taker_buy_quote_vol": [tb * c for tb, c in zip(taker_buy, closes)],
    })


class TestBinanceSymbolMapping:
    def test_major_crypto_mapping(self):
        spot, fut = get_binance_symbol_pair("BTC_USD")
        assert spot == "BTCUSDT"
        assert fut == "BTCUSDT"

        spot_eth, fut_eth = get_binance_symbol_pair("ETH_USD")
        assert spot_eth == "ETHUSDT"
        assert fut_eth == "ETHUSDT"

    def test_memecoin_mapping(self):
        spot_pepe, fut_pepe = get_binance_symbol_pair("PEPE_USD")
        assert spot_pepe == "PEPEUSDT"
        assert fut_pepe == "1000PEPEUSDT"

        spot_wif, fut_wif = get_binance_symbol_pair("WIF_USD")
        assert spot_wif == "WIFUSDT"
        assert fut_wif == "WIFUSDT"

        spot_pengu, fut_pengu = get_binance_symbol_pair("PENGU_USD")
        assert spot_pengu == "PENGUUSDT"
        assert fut_pengu == "PENGUUSDT"


class TestBinanceKlinesFetching:
    @patch("bot.binance_engine._SESSION.get")
    def test_fetch_binance_klines_spot_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            [
                1789034400000, "78000.0", "78200.0", "77900.0", "78150.0",
                "150.5", 1789035299999, "11761575.0", 500, "90.0", "7033500.0", "0"
            ]
        ] * 15
        mock_get.return_value = mock_resp

        df = fetch_binance_klines("BTC_USD", interval="15m", limit=15)
        assert len(df) == 15
        assert "time" in df.columns
        assert "close" in df.columns
        assert "taker_buy_base_vol" in df.columns
        assert df.iloc[-1]["close"] == 78150.0
        assert df.iloc[-1]["taker_buy_base_vol"] == 90.0


class TestBinanceDerivativesMetrics:
    @patch("bot.binance_engine._SESSION.get")
    def test_fetch_derivatives_success(self, mock_get):
        def side_effect(url, **kwargs):
            m = MagicMock()
            m.status_code = 200
            if "openInterest" in url:
                m.json.return_value = {"openInterest": "105500.5"}
            elif "fundingRate" in url:
                m.json.return_value = [{"fundingRate": "0.0001", "fundingTime": 1789027200000}]
            elif "topLongShortAccountRatio" in url:
                m.json.return_value = [{"longAccount": "0.62", "longShortRatio": "1.63", "shortAccount": "0.38"}]
            else:
                m.json.return_value = {}
            return m

        mock_get.side_effect = side_effect

        deriv = fetch_binance_derivatives("BTC_USD")
        assert deriv.get("open_interest") == 105500.5
        assert deriv.get("funding_rate_pct") == pytest.approx(0.01)
        assert deriv.get("top_trader_long_pct") == 62.0
        assert deriv.get("top_trader_ls_ratio") == 1.63


class TestSmartMoneyAnalysis:
    @patch("bot.binance_engine.fetch_binance_derivatives")
    def test_bullish_smart_money_analysis(self, mock_deriv):
        mock_deriv.return_value = {
            "open_interest": 95000.0,
            "funding_rate_pct": 0.008,
            "top_trader_long_pct": 65.0,
            "top_trader_ls_ratio": 1.85,
        }

        df = _sample_crypto_df(length=50, trend="up")
        metrics = analyze_binance_smart_money("SOL_USD", df)

        assert metrics.bias == "BULLISH"
        assert metrics.confidence_modifier >= 10
        assert "Taker Volume Delta" in metrics.summary_text
        assert "Top Trader Whales" in metrics.summary_text
        assert "Funding Rate" in metrics.summary_text
        assert "Futures Open Interest" in metrics.summary_text

    @patch("bot.binance_engine.fetch_binance_derivatives")
    def test_overheated_funding_warning(self, mock_deriv):
        mock_deriv.return_value = {
            "open_interest": 80000.0,
            "funding_rate_pct": 0.045,  # > 0.025%
            "top_trader_long_pct": 50.0,
            "top_trader_ls_ratio": 1.0,
        }

        df = _sample_crypto_df(length=50, trend="up")
        metrics = analyze_binance_smart_money("DOGE_USD", df)

        assert "Overheated Longs" in metrics.funding_bias
        assert "⚠️ Overheated" in metrics.summary_text


class TestEndToEndCryptoSignalWithBinance:
    @patch("bot.binance_engine.fetch_binance_derivatives")
    def test_analyze_setup_includes_smart_money_for_crypto(self, mock_deriv):
        mock_deriv.return_value = {
            "open_interest": 110000.0,
            "funding_rate_pct": 0.006,
            "top_trader_long_pct": 64.0,
            "top_trader_ls_ratio": 1.78,
        }

        df = _sample_crypto_df(length=100, trend="up")
        report = analyze_setup("BTC_USD", df, min_confidence=50)

        assert report.symbol in ("BTCUSD", "BTC_USD")
        assert report.smart_money is not None
        assert "Taker Volume Delta" in report.smart_money
        assert "Top Trader Whales" in report.smart_money

        msg = report.to_message()
        assert "Binance Smart Money" in msg
