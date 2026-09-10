import os
import json
import datetime
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from bot.paper_engine import (
    PaperStore,
    PaperPosition,
    PaperTradeHistory,
    set_virtual_balance,
    set_trade_size,
    set_leverage,
    get_paper_settings,
    open_paper_trade,
    close_paper_trade,
    check_open_trades,
    get_status_report,
    get_daily_summary_report,
    get_trade_history_report,
    close_all_open_trades,
    _load_paper_store,
    _save_paper_store,
    _load_from_cloud,
    _save_to_cloud,
)


@pytest.fixture(autouse=True)
def clean_paper_store(tmp_path, monkeypatch):
    """Resets memory store and isolates local files for each test."""
    test_store = PaperStore()
    monkeypatch.setattr("bot.paper_engine._MEMORY_STORE", test_store)
    test_file = tmp_path / "paper_store.json"
    monkeypatch.setattr("bot.paper_engine._get_local_store_path", lambda: test_file)
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "")
    yield


class TestPaperSettings:
    def test_set_virtual_balance(self):
        ok, msg = set_virtual_balance(25000.0)
        assert ok is True
        assert "25,000.00" in msg
        settings = get_paper_settings()
        assert settings["virtual_balance"] == 25000.0

    def test_set_trade_size(self):
        ok, msg = set_trade_size(500.0)
        assert ok is True
        assert "500.00" in msg
        settings = get_paper_settings()
        assert settings["trade_size_usd"] == 500.0

    def test_set_leverage(self):
        ok, msg = set_leverage(10.0)
        assert ok is True
        assert "10x" in msg
        settings = get_paper_settings()
        assert settings["leverage"] == 10.0

    def test_invalid_leverage_rejected(self):
        ok, msg = set_leverage(0.5)
        assert ok is False
        assert "between 1x and 125x" in msg

        ok2, msg2 = set_leverage(150.0)
        assert ok2 is False
        assert "between 1x and 125x" in msg2

    def test_invalid_balance_rejected(self):
        ok, msg = set_virtual_balance(-100.0)
        assert ok is False
        assert "greater than $0" in msg


class TestPaperTradeLifecycle:
    def test_open_leveraged_trade_success(self):
        set_trade_size(1000.0)
        set_leverage(10.0)
        ok, msg, pos = open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0)
        assert ok is True
        assert "10x" in msg
        assert pos["leverage"] == 10.0
        # Units = ($1000 * 10) / $50000 = 0.2 BTC
        assert pos["units"] == 0.2

        # Close at +10% price gain (55000) -> 100% ROE gain ($1000 profit on $1000 margin)
        ok_c, _, hist = close_paper_trade(pos["id"], 55000.0, reason="TP1_HIT")
        assert ok_c is True
        assert hist["pnl_usd"] == 1000.0
        assert hist["pnl_pct"] == 100.0
    def test_open_buy_trade_success(self):
        set_trade_size(1000.0)
        ok, msg, pos = open_paper_trade("BTC_USD", "BUY", 60000.0, 58000.0, 63000.0)
        assert ok is True
        assert "Paper Trade Entered" in msg
        assert pos["symbol"] == "BTC_USD"
        assert pos["action"] == "BUY"
        assert pos["entry_price"] == 60000.0
        assert pos["sl"] == 58000.0
        assert pos["tp1"] == 63000.0
        assert pos["size_usd"] == 1000.0
        assert round(pos["units"], 6) == round(1000.0 / 60000.0, 6)

    def test_open_sell_trade_success(self):
        set_trade_size(500.0)
        ok, msg, pos = open_paper_trade("ETH_USD", "SELL", 3000.0, 3100.0, 2850.0)
        assert ok is True
        assert pos["action"] == "SELL"
        assert pos["size_usd"] == 500.0

    def test_cannot_open_duplicate_trade_for_same_symbol(self):
        open_paper_trade("SOL_USD", "BUY", 150.0, 140.0, 165.0)
        ok2, msg2, pos2 = open_paper_trade("SOL_USD", "BUY", 150.0, 140.0, 165.0)
        assert ok2 is False
        assert "Active position already exists" in msg2
        assert pos2 is None

    def test_tp1_immediate_close_profit_calculation(self):
        set_virtual_balance(10000.0)
        set_trade_size(1000.0)
        ok, _, pos = open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0)
        assert ok is True

        # Simulate price reaching TP1 (55000)
        ok_close, close_msg, hist = close_paper_trade(pos["id"], 55000.0, reason="TP1_HIT")
        assert ok_close is True
        assert "Take Profit 1 Reached" in close_msg
        assert hist["exit_reason"] == "TP1_HIT"
        # +10% gain on $1,000 = +$100
        assert hist["pnl_usd"] == 100.0
        assert hist["pnl_pct"] == 10.0
        # Balance should be updated from 10000 -> 10100
        settings = get_paper_settings()
        assert settings["virtual_balance"] == 10100.0

    def test_sl_immediate_close_loss_calculation(self):
        set_virtual_balance(10000.0)
        set_trade_size(1000.0)
        ok, _, pos = open_paper_trade("BTC_USD", "BUY", 50000.0, 47500.0, 55000.0)
        assert ok is True

        # Simulate price hitting SL (47500)
        ok_close, close_msg, hist = close_paper_trade(pos["id"], 47500.0, reason="SL_HIT")
        assert ok_close is True
        assert "Stop Loss Hit" in close_msg
        assert hist["exit_reason"] == "SL_HIT"
        # -5% loss on $1,000 = -$50
        assert hist["pnl_usd"] == -50.0
        assert hist["pnl_pct"] == -5.0
        # Balance should be updated from 10000 -> 9950
        settings = get_paper_settings()
        assert settings["virtual_balance"] == 9950.0

    def test_close_by_symbol_name(self):
        open_paper_trade("PEPE_USD", "BUY", 0.00001, 0.000009, 0.000012)
        ok, msg, hist = close_paper_trade("PEPE_USD", 0.000012, reason="MANUAL_CLOSE")
        assert ok is True
        assert "Manually Closed" in msg

    def test_close_all_open_trades(self):
        open_paper_trade("BTC_USD", "BUY", 60000.0, 58000.0, 65000.0)
        open_paper_trade("ETH_USD", "BUY", 3000.0, 2900.0, 3200.0)
        count, msg = close_all_open_trades()
        assert count == 2
        assert "Closed 2 active positions" in msg
        store = _load_paper_store()
        assert len(store.positions) == 0


class TestCheckOpenTrades:
    def test_check_open_trades_triggers_tp1_long(self):
        open_paper_trade("BTC_USD", "BUY", 60000.0, 58000.0, 65000.0)

        # Mock live candle returning high reaching TP1 (65100)
        df_mock = pd.DataFrame([{
            "time": pd.to_datetime(datetime.datetime.now(datetime.timezone.utc)),
            "open": 64000.0,
            "high": 65200.0,
            "low": 63900.0,
            "close": 65100.0,
            "volume": 100.0,
        }])

        with patch("bot.paper_engine.fetch_live_candles", return_value=df_mock):
            alerts = check_open_trades()
            assert len(alerts) == 1
            assert "Take Profit 1 Reached" in alerts[0]

        store = _load_paper_store()
        assert len(store.positions) == 0
        assert len(store.history) == 1
        assert store.history[0].exit_reason == "TP1_HIT"

    def test_check_open_trades_triggers_sl_long(self):
        open_paper_trade("BTC_USD", "BUY", 60000.0, 58000.0, 65000.0)

        # Mock live candle returning low hitting SL (57800)
        df_mock = pd.DataFrame([{
            "time": pd.to_datetime(datetime.datetime.now(datetime.timezone.utc)),
            "open": 59000.0,
            "high": 59200.0,
            "low": 57800.0,
            "close": 57900.0,
            "volume": 100.0,
        }])

        with patch("bot.paper_engine.fetch_live_candles", return_value=df_mock):
            alerts = check_open_trades()
            assert len(alerts) == 1
            assert "Stop Loss Hit" in alerts[0]

        store = _load_paper_store()
        assert len(store.positions) == 0
        assert len(store.history) == 1
        assert store.history[0].exit_reason == "SL_HIT"


class TestReports:
    def test_status_report_empty(self):
        text, keyboard = get_status_report()
        assert "PAPER TRADING LIVE DASHBOARD" in text
        assert "No active paper trades" in text
        assert keyboard is None

    def test_status_report_with_open_positions(self):
        open_paper_trade("SOL_USD", "BUY", 150.0, 140.0, 170.0)
        text, keyboard = get_status_report()
        assert "SOL" in text
        assert "Active Positions:" in text
        assert "<code>1</code>" in text
        assert keyboard is not None
        assert "Close SOL" in str(keyboard)

    def test_daily_summary_report_best_and_worst_pairs(self):
        # Setup closed trades
        set_trade_size(1000.0)
        # Winner 1: BTC +$100
        open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0)
        close_paper_trade("BTC_USD", 55000.0, reason="TP1_HIT")

        # Winner 2: SOL +$150
        open_paper_trade("SOL_USD", "BUY", 100.0, 90.0, 115.0)
        close_paper_trade("SOL_USD", 115.0, reason="TP1_HIT")

        # Loser: ETH -$50
        open_paper_trade("ETH_USD", "BUY", 3000.0, 2850.0, 3300.0)
        close_paper_trade("ETH_USD", 2850.0, reason="SL_HIT")

        summary = get_daily_summary_report()
        assert "END-OF-DAY PERFORMANCE SUMMARY" in summary
        assert "Win Rate:" in summary
        assert "66.7%" in summary or "2</b> Wins / <b>1</b> Losses" in summary
        assert "Top Performing Pairs:" in summary
        assert "SOL" in summary
        assert "BTC" in summary
        assert "Underperforming Pairs:" in summary
        assert "ETH" in summary

    def test_trade_history_report(self):
        open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0)
        close_paper_trade("BTC_USD", 55000.0, reason="TP1_HIT")

        hist_report = get_trade_history_report()
        assert "Recent Paper Trade History" in hist_report
        assert "BTC" in hist_report
        assert "+$100.00" in hist_report


class TestCloudPersistence:
    @patch("bot.paper_engine.requests.get")
    def test_load_from_cloud_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        dummy_data = {
            "settings": {"virtual_balance": 15000.0, "trade_size_usd": 750.0},
            "positions": {},
            "history": [],
        }
        mock_resp.json.return_value = {"result": json.dumps(dummy_data)}
        mock_get.return_value = mock_resp

        with patch.dict(os.environ, {"UPSTASH_REDIS_REST_URL": "https://fake.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "secret"}):
            store = _load_from_cloud()
            assert store is not None
            assert store.settings.virtual_balance == 15000.0
            assert store.settings.trade_size_usd == 750.0

    @patch("bot.paper_engine.requests.post")
    def test_save_to_cloud_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        store = PaperStore()
        store.settings.virtual_balance = 12500.0

        with patch.dict(os.environ, {"UPSTASH_REDIS_REST_URL": "https://fake.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "secret"}):
            ok = _save_to_cloud(store)
            assert ok is True
            assert mock_post.called
