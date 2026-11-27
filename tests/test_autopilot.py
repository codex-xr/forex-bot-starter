import unittest
from unittest.mock import patch
import pandas as pd

from bot.autopilot import AutoPilot, autopilot, run_cron_cycle
from bot.session_bot import ALL_WATCHLIST
from bot.signal_engine import SignalReport


class TestAutoPilot(unittest.TestCase):
    def setUp(self):
        self.ap = AutoPilot(min_confidence=80, cooldown_seconds=3600)

    def test_global_autopilot_uses_75_threshold_and_vip_broadcast(self):
        self.assertEqual(autopilot.min_confidence, 75)
        self.assertTrue(autopilot.broadcast_to_vips)

    def test_cooldown_logic(self):
        self.assertFalse(self.ap.is_on_cooldown("EUR_USD", "BUY"))
        self.ap.record_alert("EUR_USD", "BUY", 85, "Breakout")
        self.assertTrue(self.ap.is_on_cooldown("EUR_USD", "BUY"))
        self.assertFalse(self.ap.is_on_cooldown("EUR_USD", "SELL"))
        self.assertFalse(self.ap.is_on_cooldown("GBP_USD", "BUY"))

    def test_paused_state(self):
        self.ap.stop()
        self.assertFalse(self.ap.is_active)
        self.assertFalse(self.ap.scan_single_symbol("EUR_USD"))
        self.ap.resume()
        self.assertTrue(self.ap.is_active)

    @patch("bot.autopilot.send_telegram_message")
    @patch("bot.autopilot.analyze_setup")
    @patch("bot.autopilot.fetch_live_candles")
    def test_ignores_wait_signals(self, mock_fetch, mock_analyze, mock_send):
        mock_fetch.return_value = pd.DataFrame({"close": [1.0] * 100})
        mock_analyze.return_value = SignalReport("EURUSD", "WAIT", 40, "Neutral", None, None, None, "No setup")

        result = self.ap.scan_single_symbol("EUR_USD")
        self.assertFalse(result)
        mock_send.assert_not_called()

    @patch("bot.autopilot.send_telegram_message")
    @patch("bot.autopilot.analyze_setup")
    @patch("bot.autopilot.fetch_live_candles")
    def test_sends_single_alert_on_buy(self, mock_fetch, mock_analyze, mock_send):
        mock_fetch.return_value = pd.DataFrame({"close": [1.0] * 100})
        mock_analyze.return_value = SignalReport("EURUSD", "BUY", 90, "Bullish", 1.0850, 1.0820, 1.0910, "Breakout")

        result = self.ap.scan_single_symbol("EUR_USD")
        self.assertTrue(result)
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("AUTO-PILOT SIGNAL DETECTED", sent_text)
        self.assertIn("EURUSD", sent_text)
        self.assertIn("BUY SETUP", sent_text)
        kb = mock_send.call_args[1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(kb[0][0]["callback_data"], "paper_enter:EUR_USD:BUY:90")

        # Second attempt should be blocked by cooldown
        mock_send.reset_mock()
        result2 = self.ap.scan_single_symbol("EUR_USD")
        self.assertFalse(result2)
        mock_send.assert_not_called()

    @patch("bot.autopilot.get_crypto_signal_recipients", return_value=[6686703329, 111222])
    @patch("bot.autopilot.send_telegram_message")
    @patch("bot.autopilot.analyze_setup")
    @patch("bot.autopilot.fetch_live_candles")
    def test_broadcasts_live_calls_to_admin_and_crypto_vips(self, mock_fetch, mock_analyze, mock_send, mock_recips):
        ap = AutoPilot(min_confidence=75, cooldown_seconds=3600, broadcast_to_vips=True)
        mock_fetch.return_value = pd.DataFrame({"close": [65000.0] * 100})
        mock_analyze.return_value = SignalReport("BTCUSD", "BUY", 82, "Bullish", 65000.0, 64000.0, 67000.0, "Breakout")

        result = ap.scan_single_symbol("BTC_USD")
        self.assertTrue(result)
        self.assertEqual(mock_send.call_count, 2)
        sent_chat_ids = [c.kwargs["chat_id"] for c in mock_send.call_args_list]
        self.assertEqual(sent_chat_ids, [6686703329, 111222])
        self.assertEqual(mock_send.call_args_list[0].kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "paper_enter:BTC_USD:BUY:82")

    @patch("bot.autopilot.autopilot")
    @patch("bot.access_control.process_due_broadcasts", return_value=["b1"])
    @patch("bot.poly_paper_engine.check_open_poly_trades", return_value=["p1", "p2"])
    @patch("bot.paper_engine.check_open_trades", return_value=["c1"])
    def test_run_cron_cycle_runs_autopilot_and_trade_checks(self, mock_ct, mock_pt, mock_bc, mock_ap):
        mock_ap.run_cycle.return_value = ["BTC_USD"]
        res = run_cron_cycle(run_autopilot=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["symbols_scanned"], len(ALL_WATCHLIST))
        self.assertEqual(res["alerts_triggered"], ["BTC_USD"])
        self.assertEqual(res["paper_trade_alerts"], 1)
        self.assertEqual(res["poly_trade_alerts"], 2)
        self.assertEqual(res["broadcasts_executed"], ["b1"])
        mock_ap.run_cycle.assert_called_once()

        mock_ap.reset_mock()
        res_trades = run_cron_cycle(run_autopilot=False)
        self.assertEqual(res_trades["symbols_scanned"], 0)
        self.assertEqual(res_trades["alerts_triggered"], [])
        mock_ap.run_cycle.assert_not_called()

    def test_golden_hours_window_wat_logic(self):
        from datetime import datetime, timezone
        ap = AutoPilot(min_confidence=75, schedule_mode="golden_wat")
        # 07:30 UTC = 08:30 WAT (Golden Morning)
        self.assertTrue(ap.is_within_scan_window(datetime(2026, 11, 23, 7, 30, tzinfo=timezone.utc)))
        # 13:30 UTC = 14:30 WAT (US Open chop -> outside)
        self.assertFalse(ap.is_within_scan_window(datetime(2026, 11, 23, 13, 30, tzinfo=timezone.utc)))
        # 15:30 UTC = 16:30 WAT (Afternoon Sweet Spot)
        self.assertTrue(ap.is_within_scan_window(datetime(2026, 11, 23, 15, 30, tzinfo=timezone.utc)))
        # 19:00 UTC = 20:00 WAT (Evening chop -> outside)
        self.assertFalse(ap.is_within_scan_window(datetime(2026, 11, 23, 19, 0, tzinfo=timezone.utc)))

        ap.set_schedule_mode("24/7")
        self.assertTrue(ap.is_within_scan_window(datetime(2026, 11, 23, 19, 0, tzinfo=timezone.utc)))

    @patch("bot.autopilot.send_telegram_message")
    @patch("bot.autopilot.analyze_setup")
    @patch("bot.autopilot.fetch_live_candles")
    def test_cloud_alert_lock_prevents_duplicate_buzz(self, mock_fetch, mock_analyze, mock_send):
        ap = AutoPilot(min_confidence=75)
        mock_fetch.return_value = pd.DataFrame({"close": [100.0] * 100})
        mock_analyze.return_value = SignalReport("SOLUSD", "BUY", 84, "Bullish", 100.0, 98.0, 102.0, "SMC Sweep")
        with patch.object(ap, "_claim_cloud_alert_lock", return_value=False):
            sent = ap.scan_single_symbol("SOL_USD")
        self.assertFalse(sent)
        mock_send.assert_not_called()

    @patch("bot.autopilot.send_telegram_message")
    @patch("bot.autopilot.analyze_setup")
    @patch("bot.autopilot.fetch_live_candles")
    def test_cycle_sends_only_the_highest_confidence_candidate(self, mock_fetch, mock_analyze, mock_send):
        """A market-wide move must not create a basket of correlated alerts."""
        ap = AutoPilot(min_confidence=75, cooldown_seconds=3600, max_alerts_per_cycle=1)
        mock_fetch.return_value = pd.DataFrame({"close": [100.0] * 100})

        def reports(symbol, *_args, **_kwargs):
            confidence = {
                "BTC_USD": 88,
                "ETH_USD": 95,
                "SOL_USD": 91,
            }.get(symbol, 40)
            action = "BUY" if confidence >= 75 else "WAIT"
            return SignalReport(symbol, action, confidence, "Bullish", 100.0, 98.0, 102.0, "Test")

        mock_analyze.side_effect = reports
        with patch("bot.autopilot.ALL_WATCHLIST", ["BTC_USD", "ETH_USD", "SOL_USD"]):
            triggered = ap.run_cycle()

        self.assertEqual(triggered, ["ETH_USD"])
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(
            mock_send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            "paper_enter:ETH_USD:BUY:95",
        )

    @patch("bot.telegram_commands.send_telegram_message")
    def test_autopilot_golden_and_247_commands(self, mock_send):
        from bot.access_control import ADMIN_CHAT_ID
        from bot.telegram_commands import handle_message

        orig_mode = autopilot.schedule_mode
        try:
            handle_message({"chat": {"id": ADMIN_CHAT_ID}, "from": {"id": ADMIN_CHAT_ID}, "text": "/autopilot golden"})
            self.assertEqual(autopilot.schedule_mode, "golden_wat")
            self.assertIn("Golden Hours Only", mock_send.call_args[0][0])

            handle_message({"chat": {"id": ADMIN_CHAT_ID}, "from": {"id": ADMIN_CHAT_ID}, "text": "/autopilot 24/7"})
            self.assertEqual(autopilot.schedule_mode, "24/7")
            self.assertIn("24/7 All Hours", mock_send.call_args[0][0])
        finally:
            autopilot.schedule_mode = orig_mode

    @patch("api.index.run_cron_cycle", return_value={"ok": True, "status": "success", "symbols_scanned": 25})
    def test_vercel_index_post_cron_without_body_runs_cycle(self, mock_cron):
        import io
        import json
        from api.index import handler

        h = object.__new__(handler)
        h.path = "/api/cron"
        h.headers = {"Content-Length": "0"}
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        h.send_response = lambda *_args: None
        h.send_header = lambda *_args: None
        h.end_headers = lambda: None

        h.do_POST()
        mock_cron.assert_called_once_with(run_autopilot=True)
        body = json.loads(h.wfile.getvalue().decode("utf-8"))
        self.assertEqual(body["status"], "success")

        mock_cron.reset_mock()
        h.path = "/api/cron?mode=trades"
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        h.do_POST()
        mock_cron.assert_called_once_with(run_autopilot=False)


