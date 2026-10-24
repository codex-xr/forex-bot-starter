import pytest
from unittest.mock import patch, MagicMock

from bot.access_control import (
    _STORE,
    generate_key,
    redeem_key,
    is_user_authorized,
    get_user_access_tier,
)
from bot.polymarket_engine import (
    PolymarketSignal,
    evaluate_polymarket_market,
    scan_polymarket_signals,
    get_cached_signal,
)
from bot.poly_paper_engine import (
    _load_poly_store,
    get_poly_settings,
    set_poly_bet_size,
    reset_poly_wallet,
    open_poly_trade,
    close_poly_trade,
    close_all_poly_trades,
    check_open_poly_trades,
    get_poly_status_report,
    get_poly_wallet_report,
    get_poly_daily_summary_report,
)
from bot.telegram_commands import handle_message, handle_callback_query


@pytest.fixture(autouse=True)
def clean_test_state(monkeypatch):
    monkeypatch.setattr("bot.poly_paper_engine._load_poly_from_cloud", lambda: None)
    monkeypatch.setattr("bot.poly_paper_engine._save_poly_to_cloud", lambda payload: None)
    monkeypatch.setattr("bot.access_control._load_from_cloud", lambda: None)
    monkeypatch.setattr("bot.access_control._save_to_cloud", lambda payload=None: None)
    _STORE["keys"].clear()
    _STORE["users"].clear()
    _STORE["schedules"].clear()
    store = _load_poly_store()
    store.users.clear()


SAMPLE_MARKET_RAW = {
    "id": "550011",
    "question": "Will Bitcoin reach $100,000 this month?",
    "category": "Crypto",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.38", "0.62"]',
    "volume24hr": 185000.0,
    "liquidityNum": 62000.0,
    "oneDayPriceChange": 0.06,
    "oneWeekPriceChange": 0.11,
    "bestBid": 0.375,
    "bestAsk": 0.385,
    "active": True,
    "closed": False,
    "acceptingOrders": True,
    "slug": "will-bitcoin-reach-100000",
}


class TestPolymarketSignalEngine:
    def test_evaluate_market_includes_confidence_pct_and_targets(self):
        sig = evaluate_polymarket_market(SAMPLE_MARKET_RAW, check_crypto_live=False)
        assert sig is not None
        assert 70 <= sig.confidence <= 95
        assert sig.action in ("BUY YES", "BUY NO")
        assert 0.38 <= sig.entry_price <= 0.82
        assert sig.tp1 > sig.entry_price
        assert sig.tp2 > sig.tp1
        assert sig.sl < sig.entry_price
        assert sig.tp1_roi_pct > 0
        assert sig.sl_roi_pct < 0

        msg = sig.to_message(index=1, bet_size=10.0)
        assert f"{sig.confidence}%</code> CONFIDENCE" in msg
        assert "TP 1 (Cash-Out):" in msg
        assert "Stop Loss:" in msg

    @patch("bot.polymarket_engine.fetch_raw_polymarket_markets")
    def test_scan_returns_top_5_sorted_by_confidence(self, mock_fetch):
        markets = []
        for i in range(8):
            m = dict(SAMPLE_MARKET_RAW)
            m["id"] = str(600000 + i)
            m["question"] = f"Will Event #{i} happen by Friday?"
            m["slug"] = f"will-event-{i}-happen"
            m["volume24hr"] = 25000.0 * (i + 1)
            markets.append(m)
        mock_fetch.return_value = markets

        msg, picks = scan_polymarket_signals(limit=5, min_confidence=65, user_id=777001, bet_size=10.0)
        assert len(picks) == 5
        assert "TOP 5 HIGH-EDGE PICKS" in msg
        # Check descending confidence order
        for idx in range(len(picks) - 1):
            assert picks[idx].confidence >= picks[idx + 1].confidence

        # Verify cached signal lookup by number 1..5
        cached_1 = get_cached_signal(777001, "1")
        assert cached_1 is not None
        assert cached_1.market_id == picks[0].market_id


class TestSeparatePolymarketPaperWallet:
    def test_default_wallet_starts_at_100_with_10_bet(self):
        settings = get_poly_settings(user_id=9001)
        assert settings["virtual_balance"] == 100.0
        assert settings["bet_size_usd"] == 10.0

    def test_set_bet_size_and_open_close_prediction(self):
        uid = 9002
        ok_bet, _ = set_poly_bet_size(20.0, user_id=uid)
        assert ok_bet is True
        assert get_poly_settings(user_id=uid)["bet_size_usd"] == 20.0

        sig = evaluate_polymarket_market(SAMPLE_MARKET_RAW, check_crypto_live=False)
        assert sig is not None
        ok_open, open_msg, pos = open_poly_trade(sig, user_id=uid)
        assert ok_open is True
        assert pos is not None
        assert pos.bet_size_usd == 20.0
        assert "POLYMARKET PAPER BET OPENED" in open_msg

        # Close manually at TP1 price -> verify profit added to $100 wallet
        ok_close, close_msg, closed = close_poly_trade("1", exit_price=sig.tp1, reason="MANUAL_CLOSE", user_id=uid)
        assert ok_close is True
        assert closed is not None
        assert closed.pnl_usd > 0
        new_bal = get_poly_settings(user_id=uid)["virtual_balance"]
        assert new_bal > 100.0

    @patch("bot.poly_paper_engine.send_telegram_message")
    @patch("bot.poly_paper_engine.get_live_share_price")
    def test_24_7_watcher_cashes_out_on_tp1_and_sl(self, mock_live_price, mock_send):
        uid = 9003
        reset_poly_wallet(100.0, user_id=uid)
        set_poly_bet_size(10.0, user_id=uid)

        sig = evaluate_polymarket_market(SAMPLE_MARKET_RAW, check_crypto_live=False)
        assert sig is not None
        open_poly_trade(sig, user_id=uid)

        # Simulate live share price hitting TP1 while user is offline
        mock_live_price.return_value = (sig.tp1 + 0.02, False, None)
        alerts = check_open_poly_trades(user_id=uid)
        assert len(alerts) == 1
        assert "TP1 HIT" in alerts[0]
        assert get_poly_settings(user_id=uid)["open_positions_count"] == 0
        assert get_poly_settings(user_id=uid)["virtual_balance"] > 100.0

        # Verify daily summary report (/psummary) lists today's closed prediction and profit
        sum_text, sum_kb = get_poly_daily_summary_report(user_id=uid)
        assert "POLYMARKET END-OF-DAY SUMMARY" in sum_text
        assert "Daily Realized PnL:" in sum_text
        assert "Top Winning Predictions Today:" in sum_text
        assert "Will Bitcoin reach $100,000" in sum_text
        assert sum_kb is not None


class TestThreeTierAccessControlAndMenus:
    def test_crypto_key_blocks_poly_and_vice_versa(self):
        # 1. Generate Crypto-only key
        c_key, _ = generate_key("30d", access_tier="crypto")
        redeem_key(1001, {"username": "crypto_sub"}, c_key)

        assert get_user_access_tier(1001) == "crypto"
        assert is_user_authorized(1001, required_tier="crypto")[0] is True
        assert is_user_authorized(1001, required_tier="poly")[0] is False

        # 2. Generate Prediction-only key
        p_key, _ = generate_key("30d", access_tier="poly")
        assert "VIP-POLY-" in p_key
        redeem_key(1002, {"username": "poly_sub"}, p_key)

        assert get_user_access_tier(1002) == "poly"
        assert is_user_authorized(1002, required_tier="poly")[0] is True
        assert is_user_authorized(1002, required_tier="crypto")[0] is False

        # 3. Generate Bundle (Both) key
        b_key, _ = generate_key("30d", access_tier="both")
        assert "VIP-BOTH-" in b_key
        redeem_key(1003, {"username": "bundle_sub"}, b_key)

        assert get_user_access_tier(1003) == "both"
        assert is_user_authorized(1003, required_tier="crypto")[0] is True
        assert is_user_authorized(1003, required_tier="poly")[0] is True

    def test_redeeming_second_tier_key_auto_upgrades_to_both(self):
        c_key, _ = generate_key("30d", access_tier="crypto")
        p_key, _ = generate_key("30d", access_tier="poly")

        redeem_key(2001, {"username": "upgrader"}, c_key)
        assert get_user_access_tier(2001) == "crypto"

        # Now user buys and redeems a Prediction key -> automatically becomes 'both'!
        redeem_key(2001, {"username": "upgrader"}, p_key)
        assert get_user_access_tier(2001) == "both"
        assert is_user_authorized(2001, required_tier="crypto")[0] is True
        assert is_user_authorized(2001, required_tier="poly")[0] is True

    @patch("bot.telegram_commands.send_telegram_message")
    def test_menu_command_shows_two_options_and_enforces_tier(self, mock_send):
        c_key, _ = generate_key("30d", access_tier="crypto")
        redeem_key(3001, {"username": "crypto_only_user"}, c_key)

        # Calling /menu shows both Crypto Menu and Prediction Menu buttons
        handle_message({"chat": {"id": 3001}, "from": {"id": 3001, "username": "crypto_only_user"}, "text": "/menu"})
        sent_text = mock_send.call_args[0][0]
        sent_kb = mock_send.call_args[1]["reply_markup"]["inline_keyboard"]
        assert "Crypto & Memecoin Menu" in sent_text
        assert "Prediction Market Menu" in sent_text
        assert sent_kb[0][0]["text"] == "📈 Crypto Menu"
        assert sent_kb[0][1]["text"] == "🔮 Prediction Menu"

        # Clicking Prediction Menu as a Crypto-only user is blocked!
        mock_send.reset_mock()
        handle_callback_query({
            "id": "cb1",
            "from": {"id": 3001, "username": "crypto_only_user"},
            "message": {"chat": {"id": 3001}},
            "data": "menu_poly",
        })
        locked_msg = mock_send.call_args[0][0]
        assert "Prediction Market Signals Locked" in locked_msg
