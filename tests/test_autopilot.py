import unittest
from unittest.mock import MagicMock, patch
import pandas as pd

from bot.autopilot import AutoPilot
from bot.signal_engine import SignalReport


class TestAutoPilot(unittest.TestCase):
    def setUp(self):
        self.ap = AutoPilot(min_confidence=80, cooldown_seconds=3600)

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

        # Second attempt should be blocked by cooldown
        mock_send.reset_mock()
        result2 = self.ap.scan_single_symbol("EUR_USD")
        self.assertFalse(result2)
        mock_send.assert_not_called()
