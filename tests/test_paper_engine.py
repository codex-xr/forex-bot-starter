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
    reset_trade_history,
    reset_paper_account,
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

    def test_open_and_close_with_unnormalized_symbol(self):
        ok, msg, pos = open_paper_trade("FLOKIUSD", "BUY", 0.00015, 0.00014, 0.00017)
        assert ok is True
        assert pos["symbol"] == "FLOKI_USD"
        assert pos["display_symbol"] == "FLOKIUSD"

        # Prevent duplicate with alternate symbol syntax
        dup_ok, dup_msg, _ = open_paper_trade("FLOKI_USD", "BUY", 0.00015, 0.00014, 0.00017)
        assert dup_ok is False
        assert "Active position already exists" in dup_msg

        # Close using shorthand / lowercase alias
        close_ok, close_msg, _ = close_paper_trade("floki", 0.00017, reason="MANUAL_CLOSE")
        assert close_ok is True
        assert "FLOKIUSD" in close_msg

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


class TestResetFunctions:
    def test_reset_trade_history(self):
        # Open and close a trade to populate history
        open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0)
        close_paper_trade("BTC_USD", 55000.0, reason="TP1_HIT")
        # Open an ongoing trade
        open_paper_trade("ETH_USD", "BUY", 3000.0, 2800.0, 3300.0)

        store = _load_paper_store()
        assert len(store.history) == 1
        assert len(store.positions) == 1

        ok, msg = reset_trade_history()
        assert ok is True
        assert "Trade History" in msg
        assert "PnL Reset" in msg

        store = _load_paper_store()
        assert len(store.history) == 0
        assert len(store.positions) == 1  # Active positions remain intact

    def test_reset_paper_account(self):
        # Populate open positions and history
        open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0)
        close_paper_trade("BTC_USD", 55000.0, reason="TP1_HIT")
        open_paper_trade("ETH_USD", "BUY", 3000.0, 2800.0, 3300.0)

        ok, msg = reset_paper_account(starting_balance=25000.0)
        assert ok is True
        assert "Paper Trading Account Fully Reset" in msg
        assert "$25,000.00" in msg

        store = _load_paper_store()
        assert len(store.positions) == 0
        assert len(store.history) == 0
        assert store.settings.virtual_balance == 25000.0


class TestMultiUserIsolation:
    def test_multi_user_isolation_history(self):
        user_a = "111000"
        user_b = "222000"

        # User A opens and closes a trade
        ok, _, pos_a = open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0, user_id=user_a)
        assert ok is True
        ok_c, _, hist_a = close_paper_trade(pos_a["id"], 55000.0, reason="TP1_HIT", user_id=user_a)
        assert ok_c is True

        # User A should see their closed trade
        report_a = get_trade_history_report(user_id=user_a)
        assert "BTCUSD" in report_a
        assert "TP1_HIT" in report_a

        # User B should see a completely blank slate
        report_b = get_trade_history_report(user_id=user_b)
        assert "No paper trade history recorded yet" in report_b
        assert "BTCUSD" not in report_b

    def test_multi_user_isolation_positions(self):
        user_a = "111000"
        user_b = "222000"

        # User A opens a position on SOL
        ok_a, _, pos_a = open_paper_trade("SOL_USD", "BUY", 150.0, 140.0, 165.0, user_id=user_a)
        assert ok_a is True

        # User B opens the same symbol SOL with their own settings - should NOT be blocked
        ok_b, _, pos_b = open_paper_trade("SOL_USD", "BUY", 150.0, 140.0, 165.0, user_id=user_b)
        assert ok_b is True
        assert pos_a["id"] != pos_b["id"]

        # User A's status report shows only User A's trade
        status_a, _ = get_status_report(user_id=user_a)
        assert "SOLUSD" in status_a
        assert "Active Positions:" in status_a
        assert "<code>1</code>" in status_a

        # User B closes their trade
        close_ok, _, _ = close_paper_trade(pos_b["id"], 165.0, reason="TP1_HIT", user_id=user_b)
        assert close_ok is True

        # User A's position is still open and unaffected
        status_a_after, _ = get_status_report(user_id=user_a)
        assert "Active Positions:" in status_a_after
        assert "<code>1</code>" in status_a_after

        # User B cannot close User A's position
        fail_close, fail_msg, _ = close_paper_trade(pos_a["id"], 165.0, user_id=user_b)
        assert fail_close is False
        assert "No open position found" in fail_msg

    def test_multi_user_isolation_balance_and_settings(self):
        user_a = "111000"
        user_b = "222000"

        set_virtual_balance(50000.0, user_id=user_a)
        set_leverage(25.0, user_id=user_a)
        set_trade_size(500.0, user_id=user_a)

        set_virtual_balance(500.0, user_id=user_b)
        set_leverage(5.0, user_id=user_b)
        set_trade_size(50.0, user_id=user_b)

        settings_a = get_paper_settings(user_id=user_a)
        settings_b = get_paper_settings(user_id=user_b)

        assert settings_a["virtual_balance"] == 50000.0
        assert settings_a["leverage"] == 25.0
        assert settings_a["trade_size_usd"] == 500.0

        assert settings_b["virtual_balance"] == 500.0
        assert settings_b["leverage"] == 5.0
        assert settings_b["trade_size_usd"] == 50.0

    def test_multi_user_reset_history_isolation(self):
        user_a = "111000"
        user_b = "222000"

        open_paper_trade("BTC_USD", "BUY", 50000.0, 48000.0, 55000.0, user_id=user_a)
        close_paper_trade("BTC_USD", 55000.0, reason="TP1_HIT", user_id=user_a)

        open_paper_trade("ETH_USD", "BUY", 3000.0, 2800.0, 3300.0, user_id=user_b)
        close_paper_trade("ETH_USD", 3300.0, reason="TP1_HIT", user_id=user_b)

        # Reset history only for User A
        reset_trade_history(user_id=user_a)

        report_a = get_trade_history_report(user_id=user_a)
        report_b = get_trade_history_report(user_id=user_b)

        assert "No paper trade history recorded yet" in report_a
        assert "ETHUSD" in report_b

    def test_legacy_data_migration(self, monkeypatch):
        admin_chat = "6686703329"
        monkeypatch.setenv("TELEGRAM_CHAT_ID", admin_chat)

        legacy_data = {
            "settings": {
                "virtual_balance": 12500.0,
                "trade_size_usd": 1500.0,
                "leverage": 20.0,
            },
            "positions": {
                "pos_123": {
                    "id": "pos_123",
                    "symbol": "BTC_USD",
                    "display_symbol": "BTC/USD",
                    "action": "BUY",
                    "entry_price": 50000.0,
                    "entry_time": "2025-01-01 12:00:00 UTC",
                    "entry_ts": 1735732800.0,
                    "tp1": 55000.0,
                    "sl": 48000.0,
                    "size_usd": 1500.0,
                    "units": 0.6,
                    "leverage": 20.0,
                    "status": "OPEN",
                }
            },
            "history": [
                {
                    "id": "pos_100",
                    "symbol": "ETH_USD",
                    "display_symbol": "ETH/USD",
                    "action": "BUY",
                    "entry_price": 3000.0,
                    "exit_price": 3300.0,
                    "entry_time": "2025-01-01 10:00:00 UTC",
                    "exit_time": "2025-01-01 11:00:00 UTC",
                    "exit_ts": 1735729200.0,
                    "exit_reason": "TP1_HIT",
                    "pnl_usd": 200.0,
                    "pnl_pct": 13.33,
                    "size_usd": 1500.0,
                    "duration_seconds": 3600.0,
                    "date_str": "2025-01-01",
                    "leverage": 20.0,
                }
            ],
        }

        store = PaperStore.from_dict(legacy_data)

        # Admin should have the legacy data assigned to them
        assert admin_chat in store.users
        admin_state = store.users[admin_chat]
        assert admin_state.settings.virtual_balance == 12500.0
        assert "pos_123" in admin_state.positions
        assert len(admin_state.history) == 1
        assert admin_state.history[0].symbol == "ETH_USD"

        # Any new user accessing gets their own isolated fresh state
        new_user = "987654321"
        new_state = store.get_user_state(new_user)
        assert len(new_state.positions) == 0
        assert len(new_state.history) == 0
        assert new_state.settings.virtual_balance == 10000.0

