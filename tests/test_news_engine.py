import unittest
from unittest.mock import MagicMock, patch
from bot.news_engine import analyze_headline_sentiment, NewsArticle, format_news_summary
from bot.signal_engine import SignalReport


class TestNewsEngine(unittest.TestCase):
    def test_bullish_trump_reserve_sentiment(self):
        title = "Trump proposes Strategic Bitcoin Reserve for US Treasury"
        score, label, impact, targets = analyze_headline_sentiment(title)
        self.assertGreater(score, 25)
        self.assertEqual(label, "Bullish")
        self.assertIn(impact, ("HIGH", "CRITICAL"))
        self.assertTrue("BTC_USD" in targets or "TRUMP_USD" in targets)

    def test_bearish_tariff_lawsuit_sentiment(self):
        title = "SEC launches major lawsuit and crackdown with tariff threats"
        score, label, impact, targets = analyze_headline_sentiment(title)
        self.assertLess(score, -25)
        self.assertEqual(label, "Bearish")
        self.assertIn(impact, ("HIGH", "CRITICAL"))

    def test_neutral_headline(self):
        title = "Developer updates library for internal testing"
        score, label, impact, targets = analyze_headline_sentiment(title)
        self.assertEqual(label, "Neutral")
        self.assertEqual(impact, "LOW")

    def test_elon_doge_mapping(self):
        title = "Elon Musk tweets about Department of Government Efficiency"
        _, _, _, targets = analyze_headline_sentiment(title)
        self.assertIn("DOGE_USD", targets)

    def test_solana_meme_mapping(self):
        title = "Solana trading surges on Raydium decentralized exchange"
        _, _, _, targets = analyze_headline_sentiment(title)
        self.assertIn("SOL_USD", targets)

    @patch("bot.news_engine.fetch_latest_news")
    def test_format_news_summary(self, mock_fetch):
        mock_fetch.return_value = [
            NewsArticle(
                id="1",
                title="Trump backs crypto council initiative",
                url="https://example.com",
                source="CoinDesk",
                body="Details inside",
                published_at="12:00 UTC",
                sentiment_score=50,
                sentiment_label="Bullish",
                impact_level="HIGH",
                affected_symbols=["BTC_USD", "TRUMP_USD"],
            )
        ]
        summary = format_news_summary(limit=1)
        self.assertIn("Breaking Crypto, Trump & Macro Catalysts", summary)
        self.assertIn("Trump backs crypto council initiative", summary)
        self.assertIn("Bullish (+50 pts)", summary)

    def test_multi_tier_tp_message(self):
        r = SignalReport(
            symbol="EURUSD",
            action="BUY",
            confidence=90,
            trend="Bullish",
            entry=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            reason="Breakout",
            tp1=1.1075,
            tp2=1.1125,
            tp3=1.1200,
            catalyst="Trump Strategic Reserve Announcement",
        )
        msg = r.to_message()
        self.assertIn("TP 1 (1:1.5)", msg)
        self.assertIn("1.10750", msg)
        self.assertIn("TP 2 (1:2.5)", msg)
        self.assertIn("1.11250", msg)
        self.assertIn("TP 3 (1:4.0)", msg)
        self.assertIn("1.12000", msg)
        self.assertIn("News Catalyst", msg)
