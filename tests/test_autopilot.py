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
