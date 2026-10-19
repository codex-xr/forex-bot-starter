import html
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.access_control import (
    is_admin,
    is_user_authorized,
    get_user_access_tier,
    format_tier_label,
    normalize_access_tier,
    generate_key,
    redeem_key,
    revoke_user,
    unban_user,
    grant_user,
    list_users_report,
    list_keys_report,
    get_user_plan_report,
    get_all_active_chat_ids,
    create_scheduled_broadcast,
    cancel_schedule,
    list_schedules_report,
    process_due_broadcasts,
    _STORE,
)
from bot.autopilot import autopilot
from bot.market_data import fetch_live_candles
from bot.news_engine import format_news_summary
from bot.paper_engine import (
    open_paper_trade,
    close_paper_trade,
    check_open_trades,
    get_status_report,
    get_daily_summary_report,
    get_trade_history_report,
    set_virtual_balance,
    set_trade_size,
    set_leverage,
    close_all_open_trades,
    get_paper_settings,
    reset_trade_history,
    reset_paper_account,
)
from bot.polymarket_engine import (
    scan_polymarket_signals,
    get_cached_signal,
)
from bot.poly_paper_engine import (
    get_poly_settings,
    set_poly_bet_size,
    reset_poly_wallet,
    open_poly_trade,
    close_poly_trade,
    close_all_poly_trades,
    check_open_poly_trades,
    get_poly_status_report,
    get_poly_wallet_report,
)
from bot.session_bot import build_session_scan, ALL_WATCHLIST
from bot.signal_engine import analyze_setup, compute_trade_levels
from bot.symbols import DISPLAY_NAMES, normalize_symbol
from bot.telegram import send_telegram_message, telegram_request


COMMANDS = {
    "/c1": "c1",
    "/c2": "c2",
    "/crypto": "c1",
    "/crypto1": "c1",
    "/crypto2": "c2",
    "/m1": "m1",
    "/m2": "m2",
    "/meme": "m1",
    "/meme1": "m1",
    "/meme2": "m2",
    "/memes": "m1",
}


def get_main_hub_keyboard(is_admin_user: bool = False) -> dict:
    """
    Main /menu Hub: Presents the two separate worlds (Crypto Menu vs Prediction Menu).
    """
    keyboard = [
        [
            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
            {"text": "🔮 Prediction Menu", "callback_data": "menu_poly"},
        ],
        [
            {"text": "ℹ️ My Subscription (/myplan)", "callback_data": "/myplan"},
            {"text": "❓ Full Help (/help)", "callback_data": "/help"},
        ],
    ]
    if is_admin_user:
        keyboard.append([
            {"text": "👑 Master Admin Panel (/admin)", "callback_data": "/admin"},
        ])
    return {"inline_keyboard": keyboard}


def get_crypto_menu_keyboard(is_admin_user: bool = False) -> dict:
    """Builds interactive Telegram inline buttons for the Crypto & Memecoin Menu."""
    keyboard = [
        # 1. Market Scans
        [
            {"text": "🚀 Major Cryptos (/c1)", "callback_data": "/c1"},
            {"text": "⚡ Hot Altcoins (/c2)", "callback_data": "/c2"},
        ],
        [
            {"text": "🐶 Top Memes (/m1)", "callback_data": "/m1"},
            {"text": "🐸 Trending Memes (/m2)", "callback_data": "/m2"},
        ],
        # 2. Paper Trading Monitor
        [
            {"text": "📊 Live Positions (/status)", "callback_data": "/status"},
            {"text": "📅 Daily PnL (/summary)", "callback_data": "/summary"},
        ],
        [
            {"text": "📜 Trade History (/history)", "callback_data": "/history"},
            {"text": "❌ Close All Trades (/closeall)", "callback_data": "/closeall"},
        ],
        # 3. Position Sizing & Leverage
        [
            {"text": "⚡ Leverage Multiplier (/setleverage)", "callback_data": "/setleverage"},
            {"text": "⚙️ Trade Margin (/setsize)", "callback_data": "/setsize"},
        ],
        [
            {"text": "💼 Virtual Capital (/setbalance)", "callback_data": "/setbalance"},
            {"text": "🧹 Reset History & PnL (/resethistory)", "callback_data": "/resethistory"},
        ],
        # 4. Intelligence & Automation
        [
            {"text": "📰 Breaking News (/news)", "callback_data": "/news"},
            {"text": "🤖 Auto-Pilot 24/7 (/autopilot)", "callback_data": "/autopilot"},
        ],
        # 5. Account & Navigation
        [
            {"text": "🔄 Reset Crypto Wallet (/resetaccount)", "callback_data": "/resetaccount"},
            {"text": "🔙 Back to Main Menu (/menu)", "callback_data": "/menu"},
        ],
    ]
    if is_admin_user:
        keyboard.append([
            {"text": "👑 Master Admin Panel (/admin)", "callback_data": "/admin"},
        ])
    return {"inline_keyboard": keyboard}


def get_poly_menu_keyboard(is_admin_user: bool = False) -> dict:
    """Builds interactive Telegram inline buttons for the Polymarket Prediction Menu."""
    keyboard = [
        [
            {"text": "🔮 Scan Top 5 Predictions (/poly)", "callback_data": "/poly"},
        ],
        [
            {"text": "🚀 Crypto Predictions (/poly crypto)", "callback_data": "/poly crypto"},
            {"text": "🏆 Sports & Events (/poly sports)", "callback_data": "/poly sports"},
        ],
        [
            {"text": "📊 Active Bets (/pstatus)", "callback_data": "/pstatus"},
            {"text": "💼 Prediction Wallet (/pwallet)", "callback_data": "/pwallet"},
        ],
        [
            {"text": "💵 Set Bet Amount (/polybet)", "callback_data": "/polybet"},
            {"text": "❌ Cash Out All (/pclose all)", "callback_data": "/pclose all"},
        ],
        [
            {"text": "🔄 Reset Wallet (/preset)", "callback_data": "/preset"},
            {"text": "🔙 Back to Main Menu (/menu)", "callback_data": "/menu"},
        ],
    ]
    if is_admin_user:
        keyboard.append([
            {"text": "👑 Master Admin Panel (/admin)", "callback_data": "/admin"},
        ])
    return {"inline_keyboard": keyboard}


# Backward compatibility alias
def get_menu_keyboard(is_admin_user: bool = False) -> dict:
    return get_main_hub_keyboard(is_admin_user=is_admin_user)


def get_admin_keyboard() -> dict:
    """Dedicated Admin Control Panel buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "👥 User Dashboard (/users)", "callback_data": "/users"},
                {"text": "🔑 VIP Keys (/keys)", "callback_data": "/keys"},
            ],
            [
                {"text": "🔑 +30d Crypto Key", "callback_data": "/genkey 30d crypto"},
                {"text": "🔮 +30d Poly Key", "callback_data": "/genkey 30d poly"},
                {"text": "👑 +30d Bundle Key", "callback_data": "/genkey 30d both"},
            ],
            [
                {"text": "📈 Open Crypto Menu", "callback_data": "menu_crypto"},
                {"text": "🔮 Open Prediction Menu", "callback_data": "menu_poly"},
            ],
            [
                {"text": "📊 Crypto Trades (/status)", "callback_data": "/status"},
                {"text": "🔮 Poly Bets (/pstatus)", "callback_data": "/pstatus"},
            ],
            [
                {"text": "⏰ Active Schedules (/schedules)", "callback_data": "/schedules"},
                {"text": "🤖 Auto-Pilot (/autopilot)", "callback_data": "/autopilot"},
            ],
            [
                {"text": "📱 Main Menu (/menu)", "callback_data": "/menu"},
                {"text": "❓ Help Guide (/help)", "callback_data": "/help"},
            ],
        ]
    }


MAIN_HUB_TEXT = """🚀 <b>Welcome to the VIP Signal & Prediction Terminal</b>

Choose which terminal you want to open below:

📈 <b>1. Crypto & Memecoin Menu</b>
• Institutional Crypto & Memecoin signals (<code>/c1</code>, <code>/c2</code>, <code>/m1</code>, <code>/m2</code>)
• Dedicated Crypto Paper Wallet & 24/7 TP1/SL Watcher

🔮 <b>2. Prediction Market Menu (Polymarket)</b>
• High-Edge Polymarket Predictions with Confidence % (<code>/poly</code>)
• Separate Prediction Paper Wallet (<code>$100</code> balance, <code>/polybet</code>, <code>/penter</code>, <code>/pstatus</code>, <code>/pwallet</code>) & 24/7 Auto Cash-Out

👇 <b>Tap a menu below to begin:</b>"""


CRYPTO_MENU_TEXT = """📈 <b>Crypto & Memecoin Signal Menu</b>

Tap any button below or type a command:

🚀 <b>Crypto Market Scans:</b>
• <code>/c1</code> — Major Cryptos (BTC, ETH, SOL, XRP, DOGE, ADA)
• <code>/c2</code> — High-Momentum Altcoins (BNB, AVAX, LINK, SUI, NEAR, LTC)

🐶🐸 <b>High-Volatility Memecoin Scans:</b>
• <code>/m1</code> — Top Memes (WIF, PEPE, SHIB, BONK, FLOKI, BRETT, ANSEM)
• <code>/m2</code> — Trending Memes (TRUMP, BOME, PENGU, MOG, PEOPLE, ELON)

💼 <b>Crypto Paper Trading (24/7 TP1/SL Watched):</b>
• <code>/status</code> — Active open trades, live prices & floating PnL
• <code>/summary</code> — End-of-day performance & daily PnL
• <code>/setbalance &lt;amt&gt;</code> — Set virtual account capital
• <code>/setsize &lt;amt&gt;</code> — Set entry margin size per trade
• <code>/setleverage &lt;multiplier&gt;</code> — Set trade leverage (e.g. <code>25x</code>)
• <code>/enter &lt;symbol&gt;</code> — Enter a paper trade manually (e.g. <code>/enter BTC</code>)
• <code>/close &lt;symbol&gt;</code> — Close an active paper trade (or <code>/closeall</code>)
• <code>/history</code> — View recent closed trade history
• <code>/resethistory</code> — Clear trade history & reset PnL stats
• <code>/resetaccount [amt]</code> — Full crypto paper account reset"""


POLY_MENU_TEXT = """🔮 <b>Polymarket Prediction Signal Menu</b>

Tap any button below or use the 6 simple commands:

🎯 <b>Prediction Commands:</b>
• <code>/poly</code> — Scan Top 5 highest-confidence Polymarket predictions (or <code>/poly crypto</code>, <code>/poly sports</code>, <code>/poly trump</code>)
• <code>/polybet &lt;amount&gt;</code> — Set bet amount per prediction (default: <code>$10</code>)
• <code>/penter &lt;number&gt;</code> — Enter a prediction from your scan (e.g. <code>/penter 1</code>) or tap the <b>Enter Bet</b> button under any signal
• <code>/pstatus</code> — View active predictions, live odds & floating profit/loss
• <code>/pclose &lt;number&gt;</code> — Cash out a prediction early (or <code>/pclose all</code>)
• <code>/pwallet</code> — View your separate Polymarket balance, bet size & win rate (or <code>/pwallet reset 100</code>)"""


USER_HELP_TEXT = (
    MAIN_HUB_TEXT
    + "\n\n────────────────────\n\n"
    + CRYPTO_MENU_TEXT
    + "\n\n────────────────────\n\n"
    + POLY_MENU_TEXT
)

ADMIN_HELP_TEXT = USER_HELP_TEXT + """

────────────────────

👑 <b>Master Admin Control Panel:</b>
• <code>/admin</code> — Open Admin Interactive Dashboard
• <code>/genkey &lt;dur&gt; [crypto|poly|both]</code> — Generate VIP Key:
  • Crypto only: <code>/genkey 30d crypto</code>
  • Prediction only: <code>/genkey 30d poly</code>
  • Bundle (Both): <code>/genkey 30d both</code>
• <code>/grant &lt;user_id&gt; &lt;dur&gt; [crypto|poly|both]</code> — Directly grant access
• <code>/users</code> — View all registered users, tiers & stats
• <code>/revoke &lt;user_id&gt;</code> — Terminate/ban a user's access
• <code>/unban &lt;user_id&gt;</code> — Restore an account / unban a user
• <code>/keys</code> — View available & redeemed keys
• <code>/broadcast &lt;message&gt;</code> — Send instant announcement to all users
• <code>/schedule &lt;timing&gt; &lt;message&gt;</code> — Schedule one-time or recurring broadcast
• <code>/schedules</code> — View all active scheduled broadcasts
• <code>/cancelschedule &lt;id&gt;</code> — Cancel a scheduled broadcast
"""

UNAUTHORIZED_HELP_TEXT = """🔒 <b>VIP Crypto & Prediction Market Signal Bot</b>

<i>This is a private VIP signal system. Access is restricted to authorized subscribers only.</i>

🔑 <b>Have an Activation Key?</b>
Activate your account instantly with:
👉 <code>/redeem &lt;YOUR_KEY&gt;</code>
<i>(Example: <code>/redeem VIP-8842-30D</code>)</i>

ℹ️ <b>Available Commands:</b>
• <code>/redeem &lt;KEY&gt;</code> — Activate your Crypto, Prediction, or Bundle key
• <code>/myplan</code> — Check subscription status
• <code>/menu</code> — Open main menu
• <code>/help</code> — Show this message

<i>To obtain an activation key, please contact the Administrator.</i>
"""


def _send_tier_locked_message(chat_id: int | str, required_tier: str) -> None:
    if required_tier == "poly":
        msg = (
            "🔒 <b>Prediction Market Signals Locked</b>\n\n"
            "Your current VIP subscription is for <b>📈 Crypto & Memecoins Only</b>.\n\n"
            "To unlock the <b>🔮 Polymarket Prediction Engine</b>, please redeem a Prediction or Bundle key:\n"
            "👉 <code>/redeem &lt;PREDICTION_KEY&gt;</code>\n\n"
            "<i>Contact the Administrator to get a Prediction Market access code!</i>"
        )
        kb = {"inline_keyboard": [
            [{"text": "📈 Go to Crypto Menu", "callback_data": "menu_crypto"}],
            [{"text": "📱 Main Menu (/menu)", "callback_data": "/menu"}],
        ]}
    else:
        msg = (
            "🔒 <b>Crypto & Memecoin Signals Locked</b>\n\n"
            "Your current VIP subscription is for <b>🔮 Polymarket Predictions Only</b>.\n\n"
            "To unlock the <b>📈 Crypto & Memecoin Signal Engine</b>, please redeem a Crypto or Bundle key:\n"
            "👉 <code>/redeem &lt;CRYPTO_KEY&gt;</code>\n\n"
            "<i>Contact the Administrator to get a Crypto access code!</i>"
        )
        kb = {"inline_keyboard": [
            [{"text": "🔮 Go to Prediction Menu", "callback_data": "menu_poly"}],
            [{"text": "📱 Main Menu (/menu)", "callback_data": "/menu"}],
        ]}
    send_telegram_message(msg, chat_id=chat_id, reply_markup=kb)


def _verify_tier_access(chat_id: int | str, user_info: dict | None, required_tier: str) -> bool:
    """
    Verifies both general account authorization AND specific tier authorization ('crypto' or 'poly').
    Sends appropriate error message and returns False if blocked.
    """
    is_auth, auth_status = is_user_authorized(chat_id, user_info, required_tier=required_tier)
    if is_auth:
        return True

    if auth_status == "revoked":
        send_telegram_message("🚫 <b>Access Revoked:</b> Your access to this bot has been terminated by the Administrator.", chat_id=chat_id)
    elif auth_status == "expired":
        send_telegram_message("⏳ <b>Subscription Expired:</b> Your VIP access has expired. Please redeem a new key with <code>/redeem &lt;KEY&gt;</code> or contact the Admin.", chat_id=chat_id)
    elif auth_status.startswith("tier_locked:"):
        _send_tier_locked_message(chat_id, required_tier)
    else:
        send_telegram_message(
            "🔒 <b>Access Restricted</b>\n\n"
            "You need an active VIP activation key to use this bot.\n\n"
            "To activate access, use:\n"
            "👉 <code>/redeem &lt;YOUR_KEY&gt;</code>\n\n"
            "<i>To obtain a key, please contact the Administrator.</i>",
            chat_id=chat_id,
        )
    return False


def handle_callback_query(callback_query: dict) -> None:
    """Handles button clicks from inline Telegram keyboards."""
    query_id = callback_query.get("id")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    user_info = callback_query.get("from", {})
    data = (callback_query.get("data") or "").strip()

    if not chat_id and user_info:
        chat_id = user_info.get("id")

    if query_id:
        try:
            telegram_request("answerCallbackQuery", {"callback_query_id": query_id}, timeout=5)
        except Exception as exc:
            print(f"[CallbackQuery] Ack error: {exc}")

    if not chat_id or not data:
        return

    admin_flag = is_admin(chat_id) or (user_info and is_admin(user_info.get("id")))

    # 0. Handle Sub-Menu Navigation Buttons (Crypto Menu vs Prediction Menu)
    if data == "menu_crypto":
        if not _verify_tier_access(chat_id, user_info, "crypto"):
            return
        send_telegram_message(
            CRYPTO_MENU_TEXT,
            chat_id=chat_id,
            reply_markup=get_crypto_menu_keyboard(is_admin_user=bool(admin_flag)),
        )
        return

    if data == "menu_poly":
        if not _verify_tier_access(chat_id, user_info, "poly"):
            return
        send_telegram_message(
            POLY_MENU_TEXT,
            chat_id=chat_id,
            reply_markup=get_poly_menu_keyboard(is_admin_user=bool(admin_flag)),
        )
        return

    # 1. Handle 1-Click Crypto Paper Trade Entry
    if data.startswith("paper_enter:"):
        if get_user_access_tier(chat_id) == "poly":
            _send_tier_locked_message(chat_id, "crypto")
            return
        parts = data.split(":")
        if len(parts) >= 3:
            symbol = normalize_symbol(parts[1])
            action = parts[2]
            try:
                candles = fetch_live_candles(symbol)
                report = analyze_setup(symbol, candles, min_confidence=60)
                if report.action != action or not report.stop_loss or not (report.tp1 or report.take_profit):
                    report = compute_trade_levels(symbol, action, candles)
                curr_close = float(candles.iloc[-1]["close"])
                entry = report.entry or curr_close
                sl = report.stop_loss or (entry * 0.985 if action == "BUY" else entry * 1.015)
                risk_dist = abs(entry - sl)
                tp1 = report.tp1 or report.take_profit or (entry + risk_dist if action == "BUY" else entry - risk_dist)
                order_type = getattr(report, "order_type", "MARKET")
                ok, enter_msg, _ = open_paper_trade(
                    symbol, action, entry, sl, tp1, user_id=chat_id,
                    order_type=order_type, current_price=curr_close,
                )
                send_telegram_message(enter_msg, chat_id=chat_id)
            except Exception as e:
                send_telegram_message(f"❌ Failed to enter paper trade for {symbol}: {e}", chat_id=chat_id)
        return

    # 2. Handle Manual Crypto Paper Trade Close from Status Dashboard
    if data.startswith("paper_close:"):
        if get_user_access_tier(chat_id) == "poly":
            _send_tier_locked_message(chat_id, "crypto")
            return
        pos_id = data.split(":", 1)[1]
        try:
            from bot.paper_engine import _load_paper_store
            store = _load_paper_store()
            ustate = store.get_user_state(chat_id)
            pos = ustate.positions.get(pos_id)
            curr_price = pos.entry_price if pos else 0.0
            if pos:
                try:
                    df = fetch_live_candles(pos.symbol)
                    if not df.empty:
                        curr_price = float(df.iloc[-1]["close"])
                except Exception:
                    pass
            ok, close_msg, _ = close_paper_trade(pos_id, curr_price if curr_price > 0 else 1.0, reason="MANUAL_CLOSE", user_id=chat_id)
            send_telegram_message(close_msg, chat_id=chat_id)
        except Exception as e:
            send_telegram_message(f"❌ Failed to close position: {e}", chat_id=chat_id)
        return

    # 3. Handle 1-Click Polymarket Prediction Bet Entry
    if data.startswith("poly_enter:"):
        if not _verify_tier_access(chat_id, user_info, "poly"):
            return
        target_ref = data.split(":", 1)[1].strip()
        sig = get_cached_signal(chat_id, target_ref)
        if not sig:
            send_telegram_message(
                "⚠️ Signal expired from quick cache. Run <code>/poly</code> for fresh live odds!",
                chat_id=chat_id,
            )
            return
        ok, enter_msg, _ = open_poly_trade(sig, user_id=chat_id)
        send_telegram_message(
            enter_msg,
            chat_id=chat_id,
            reply_markup={"inline_keyboard": [
                [
                    {"text": "📊 Active Bets (/pstatus)", "callback_data": "/pstatus"},
                    {"text": "💼 Prediction Wallet (/pwallet)", "callback_data": "/pwallet"},
                ],
                [
                    {"text": "🔮 Prediction Menu", "callback_data": "menu_poly"},
                ],
            ]},
        )
        return

    # 4. Handle 1-Click Polymarket Prediction Cash-Out
    if data.startswith("poly_close:"):
        if not _verify_tier_access(chat_id, user_info, "poly"):
            return
        pos_id = data.split(":", 1)[1].strip()
        ok, close_msg, _ = close_poly_trade(pos_id, reason="MANUAL_CLOSE", user_id=chat_id)
        send_telegram_message(
            close_msg,
            chat_id=chat_id,
            reply_markup={"inline_keyboard": [
                [
                    {"text": "📊 Active Bets (/pstatus)", "callback_data": "/pstatus"},
                    {"text": "💼 Prediction Wallet (/pwallet)", "callback_data": "/pwallet"},
                ],
                [
                    {"text": "🔮 Scan More (/poly)", "callback_data": "/poly"},
                ],
            ]},
        )
        return

    # Normalize data as standard command with slash if needed
    cmd_text = data if data.startswith("/") else f"/{data}"

    # Route button action as standard command message
    synthetic_msg = {
        "chat": {"id": chat_id},
        "from": user_info,
        "text": cmd_text,
    }
    handle_message(synthetic_msg)


def handle_message(message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    user_info = message.get("from", {})
    raw_text = (message.get("text") or "").strip()
    text = raw_text.lower()

    if not chat_id or not text:
        return

    parts = raw_text.split()
    command = parts[0].lower()
    subcmd = parts[1] if len(parts) > 1 else ""

    print(f"Received command '{raw_text}' from chat {chat_id} (@{user_info.get('username')})")

    try:
        admin_flag = is_admin(chat_id) or (user_info and is_admin(user_info.get("id")))

        # -------------------------------------------------------------
        # 1. Public Account, Hub Menu & Help Commands
        # -------------------------------------------------------------
        if command in {"/start", "/menu", "/dashboard", "/dash", "/signals"}:
            is_auth, _ = is_user_authorized(chat_id, user_info)
            if admin_flag or is_auth:
                send_telegram_message(
                    MAIN_HUB_TEXT,
                    chat_id=chat_id,
                    reply_markup=get_main_hub_keyboard(is_admin_user=bool(admin_flag)),
                )
            else:
                send_telegram_message(UNAUTHORIZED_HELP_TEXT, chat_id=chat_id)
            return

        if command in {"/help", "/commands", "/cmds"}:
            is_auth, _ = is_user_authorized(chat_id, user_info)
            if admin_flag:
                send_telegram_message(
                    ADMIN_HELP_TEXT,
                    chat_id=chat_id,
                    reply_markup=get_main_hub_keyboard(is_admin_user=True),
                )
            elif is_auth:
                send_telegram_message(
                    USER_HELP_TEXT,
                    chat_id=chat_id,
                    reply_markup=get_main_hub_keyboard(is_admin_user=False),
                )
            else:
                send_telegram_message(UNAUTHORIZED_HELP_TEXT, chat_id=chat_id)
            return

        if command in {"/cryptomenu", "/cmenu"}:
            if not _verify_tier_access(chat_id, user_info, "crypto"):
                return
            send_telegram_message(
                CRYPTO_MENU_TEXT,
                chat_id=chat_id,
                reply_markup=get_crypto_menu_keyboard(is_admin_user=bool(admin_flag)),
            )
            return

        if command in {"/polymenu", "/pmenu", "/predictionmenu"}:
            if not _verify_tier_access(chat_id, user_info, "poly"):
                return
            send_telegram_message(
                POLY_MENU_TEXT,
                chat_id=chat_id,
                reply_markup=get_poly_menu_keyboard(is_admin_user=bool(admin_flag)),
            )
            return

        if command == "/redeem":
            if not subcmd:
                send_telegram_message(
                    "⚠️ Please provide an activation key.\n\nUsage: <code>/redeem &lt;YOUR_KEY&gt;</code>\nExample: <code>/redeem VIP-A1B2-30D</code>",
                    chat_id=chat_id,
                )
                return
            ok, msg = redeem_key(chat_id, user_info, subcmd)
            send_telegram_message(
                msg,
                chat_id=chat_id,
                reply_markup=get_main_hub_keyboard(is_admin_user=bool(admin_flag)) if ok else None,
            )
            return

        if command in {"/myplan", "/account", "/plan"}:
            send_telegram_message(
                get_user_plan_report(chat_id),
                chat_id=chat_id,
                reply_markup=get_main_hub_keyboard(is_admin_user=bool(admin_flag)),
            )
            return

        # -------------------------------------------------------------
        # 2. Admin Only Commands
        # -------------------------------------------------------------
        if command in {
            "/admin", "/genkey", "/users", "/subscribers", "/members", "/revoke", "/ban",
            "/unban", "/restore", "/grant", "/keys", "/broadcast",
            "/schedule", "/schedules", "/cancelschedule", "/delschedule", "/unschedule"
        }:
            if not admin_flag:
                send_telegram_message("🚫 <b>Access Denied:</b> This command is reserved for the Master Administrator.", chat_id=chat_id)
                return

            if command == "/admin":
                admin_msg = (
                    "👑 <b>Master Administrator Control Panel</b>\n\n"
                    "• <b>Status:</b> 🟢 Online (Cloud Database Connected)\n"
                    "• <b>Access:</b> Full Admin Authorization (Crypto + Polymarket)\n\n"
                    "<b>Generate 3-Tier Access Keys:</b>\n"
                    "• <code>/genkey 30d crypto</code> — Crypto Only Key\n"
                    "• <code>/genkey 30d poly</code> — Prediction Only Key\n"
                    "• <code>/genkey 30d both</code> — Bundle Key (Both)\n\n"
                    "<b>Admin Controls:</b>\n"
                    "• <code>/users</code> — View all registered users & tiers\n"
                    "• <code>/grant &lt;id&gt; &lt;dur&gt; [crypto|poly|both]</code> — Direct VIP Grant\n"
                    "• <code>/revoke &lt;id&gt;</code> — Ban / Terminate User Access\n"
                    "• <code>/unban &lt;id&gt;</code> — Restore User Access\n"
                    "• <code>/keys</code> — View Available & Redeemed Keys\n"
                    "• <code>/broadcast &lt;msg&gt;</code> — Send Announcement to All\n"
                    "• <code>/schedule &lt;timing&gt; &lt;msg&gt;</code> — Schedule Announcement\n"
                    "• <code>/schedules</code> — View Active Schedules\n"
                    "• <code>/cancelschedule &lt;id&gt;</code> — Cancel Scheduled Alert"
                )
                send_telegram_message(admin_msg, chat_id=chat_id, reply_markup=get_admin_keyboard())
                return

            if command == "/genkey":
                dur = subcmd if subcmd else "30d"
                note_parts = parts[2:] if len(parts) > 2 else []
                tier_arg = None
                if note_parts and note_parts[0].lower() in ("crypto", "poly", "polymarket", "predict", "prediction", "predictions", "both", "all", "bundle"):
                    tier_arg = normalize_access_tier(note_parts[0])
                    note = " ".join(note_parts[1:])
                else:
                    note = " ".join(note_parts)

                key_code, label = generate_key(dur, note, access_tier=tier_arg)
                key_tier = _STORE["keys"].get(key_code, {}).get("access_tier", "crypto")
                tier_display = format_tier_label(key_tier)
                msg = (
                    f"🔑 <b>New VIP Activation Key Generated!</b>\n\n"
                    f"• <b>Key:</b> <code>{key_code}</code>\n"
                    f"• <b>Package:</b> <b>{html.escape(tier_display)}</b>\n"
                    f"• <b>Duration:</b> <b>{label}</b>\n\n"
                    f"<i>Send this key to your subscriber. They can activate it with:</i>\n"
                    f"<code>/redeem {key_code}</code>"
                )
                send_telegram_message(msg, chat_id=chat_id)
                return

            if command in {"/users", "/subscribers", "/members"}:
                send_telegram_message(list_users_report(), chat_id=chat_id)
                return

            if command == "/keys":
                send_telegram_message(list_keys_report(), chat_id=chat_id)
                return

            if command in {"/revoke", "/ban"}:
                if not subcmd:
                    send_telegram_message("⚠️ Please specify user ID or username to revoke.\n\nUsage: <code>/revoke &lt;user_id or @username&gt;</code>", chat_id=chat_id)
                    return
                ok, msg = revoke_user(subcmd)
                send_telegram_message(msg, chat_id=chat_id)
                return

            if command in {"/unban", "/restore"}:
                if not subcmd:
                    send_telegram_message("⚠️ Please specify user ID or username to unban.\n\nUsage: <code>/unban &lt;user_id or @username&gt;</code>", chat_id=chat_id)
                    return
                ok, msg = unban_user(subcmd)
                send_telegram_message(msg, chat_id=chat_id)
                return

            if command == "/grant":
                if not subcmd:
                    send_telegram_message(
                        "⚠️ Usage: <code>/grant &lt;user_id or @username&gt; [duration] [crypto|poly|both]</code>\n"
                        "Example: <code>/grant 123456789 30d poly</code>",
                        chat_id=chat_id,
                    )
                    return
                dur = parts[2] if len(parts) > 2 else "30d"
                tier_arg = parts[3] if len(parts) > 3 else "crypto"
                ok, msg = grant_user(subcmd, dur, access_tier=tier_arg)
                send_telegram_message(msg, chat_id=chat_id)
                return

            if command == "/broadcast":
                if len(parts) < 2:
                    send_telegram_message("⚠️ Usage: <code>/broadcast &lt;message text&gt;</code>", chat_id=chat_id)
                    return

                msg_body = raw_text[len(parts[0]):].strip()
                if msg_body.startswith("<") and msg_body.endswith(">"):
                    msg_body = msg_body[1:-1].strip()

                safe_msg = html.escape(msg_body)
                broadcast_text = f"📢 <b>ADMIN ANNOUNCEMENT</b>\n\n{safe_msg}"

                recipient_ids = get_all_active_chat_ids()
                sent_count = 0
                for cid in recipient_ids:
                    try:
                        send_telegram_message(broadcast_text, chat_id=cid)
                        sent_count += 1
                        time.sleep(0.04)
                    except Exception as e:
                        print(f"Failed broadcast to {cid}: {e}")
                send_telegram_message(f"✅ Broadcast sent to <b>{sent_count}</b> users.", chat_id=chat_id)
                return

            if command == "/schedule":
                if len(parts) < 3:
                    send_telegram_message(
                        "⚠️ <b>Usage:</b> <code>/schedule &lt;timing&gt; &lt;message&gt;</code>\n\n"
                        "<b>Examples:</b>\n"
                        "• One-time: <code>/schedule in 2h Market Open alert!</code>\n"
                        "• Recurring: <code>/schedule every 24h Daily market check</code>\n"
                        "• Daily UTC: <code>/schedule daily 08:30 Good morning traders!</code>",
                        chat_id=chat_id,
                    )
                    return

                sub_lower = subcmd.lower()
                if sub_lower in {"in", "every", "daily"} and len(parts) >= 4:
                    time_expr = f"{parts[1]} {parts[2]}"
                    msg_start_idx = 3
                else:
                    time_expr = parts[1]
                    msg_start_idx = 2

                msg_text = " ".join(parts[msg_start_idx:])
                if msg_text.startswith("<") and msg_text.endswith(">"):
                    msg_text = msg_text[1:-1].strip()

                ok, resp = create_scheduled_broadcast(time_expr, msg_text, target="all")
                send_telegram_message(resp, chat_id=chat_id)
                return

            if command in {"/schedules", "/scheduled"}:
                send_telegram_message(list_schedules_report(), chat_id=chat_id)
                return

            if command in {"/cancelschedule", "/delschedule", "/unschedule"}:
                if not subcmd:
                    send_telegram_message("⚠️ Usage: <code>/cancelschedule &lt;Schedule ID&gt;</code>\nExample: <code>/cancelschedule SB-101</code>", chat_id=chat_id)
                    return
                ok, resp = cancel_schedule(subcmd)
                send_telegram_message(resp, chat_id=chat_id)
                return

        # -------------------------------------------------------------
        # 3. Polymarket Prediction Engine Commands (Requires 'poly' Tier)
        # -------------------------------------------------------------
        if command in {
            "/poly", "/p1", "/predict", "/predictions",
            "/polybet", "/pbet",
            "/penter", "/polyenter",
            "/pstatus", "/polystatus", "/pbets",
            "/pclose", "/polyclose", "/pcashout",
            "/pwallet", "/polywallet", "/pstats", "/preset",
        }:
            if not _verify_tier_access(chat_id, user_info, "poly"):
                return

            if command in {"/poly", "/p1", "/predict", "/predictions"}:
                topic = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
                send_telegram_message("🔮 Scanning Polymarket live odds & order flow. One moment...", chat_id=chat_id)
                try:
                    check_open_poly_trades(user_id=chat_id)
                except Exception:
                    pass

                psettings = get_poly_settings(user_id=chat_id)
                bet_amt = psettings.get("bet_size_usd", 10.0)
                scan_msg, picks = scan_polymarket_signals(
                    query=topic,
                    limit=5,
                    min_confidence=70,
                    user_id=chat_id,
                    bet_size=bet_amt,
                )
                buttons = []
                for idx, sig in enumerate(picks, start=1):
                    short_q = sig.question[:24] + "..." if len(sig.question) > 24 else sig.question
                    btn_label = f"🎯 Enter #{idx}: {sig.action} @ {sig.entry_price * 100:.0f}¢ (${bet_amt:g})"
                    buttons.append([{"text": btn_label, "callback_data": f"poly_enter:{idx}"}])

                buttons.append([
                    {"text": "📊 Active Bets (/pstatus)", "callback_data": "/pstatus"},
                    {"text": "💼 Wallet (/pwallet)", "callback_data": "/pwallet"},
                ])
                buttons.append([
                    {"text": "🔮 Prediction Menu", "callback_data": "menu_poly"},
                    {"text": "📱 Main Menu", "callback_data": "/menu"},
                ])
                send_telegram_message(scan_msg, chat_id=chat_id, reply_markup={"inline_keyboard": buttons})
                return

            if command in {"/polybet", "/pbet"}:
                if not subcmd:
                    psettings = get_poly_settings(user_id=chat_id)
                    bet_kb = [
                        [
                            {"text": "$5", "callback_data": "/polybet 5"},
                            {"text": "$10", "callback_data": "/polybet 10"},
                            {"text": "$25", "callback_data": "/polybet 25"},
                        ],
                        [
                            {"text": "$50", "callback_data": "/polybet 50"},
                            {"text": "$100", "callback_data": "/polybet 100"},
                            {"text": "🔮 Prediction Menu", "callback_data": "menu_poly"},
                        ],
                    ]
                    send_telegram_message(
                        f"💵 <b>Current Polymarket Bet Amount:</b> <code>${psettings['bet_size_usd']:,.2f}</code> per pick\n"
                        f"• <b>Prediction Wallet Balance:</b> <code>${psettings['virtual_balance']:,.2f}</code>\n\n"
                        f"Tap a button below or type: <code>/polybet &lt;amount&gt;</code>\n"
                        f"Example: <code>/polybet 10</code>",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": bet_kb},
                    )
                    return
                clean_val = subcmd.replace("$", "").replace(",", "").strip()
                try:
                    val = float(clean_val)
                    ok, msg = set_poly_bet_size(val, user_id=chat_id)
                    send_telegram_message(
                        msg,
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": [[{"text": "🔮 Scan Predictions (/poly)", "callback_data": "/poly"}]]},
                    )
                except ValueError:
                    send_telegram_message("❌ Invalid bet amount. Example: <code>/polybet 10</code>", chat_id=chat_id)
                return

            if command in {"/penter", "/polyenter"}:
                if not subcmd:
                    send_telegram_message(
                        "⚠️ Specify which prediction number from your <code>/poly</code> scan to enter.\n\n"
                        "Usage: <code>/penter &lt;1-5&gt;</code>\n"
                        "Example: <code>/penter 1</code>",
                        chat_id=chat_id,
                    )
                    return
                sig = get_cached_signal(chat_id, subcmd)
                if not sig:
                    send_telegram_message(
                        "⚠️ Could not find that signal number in your recent scan. Run <code>/poly</code> first and tap <b>Enter</b> or type <code>/penter 1</code>!",
                        chat_id=chat_id,
                    )
                    return
                ok, enter_msg, _ = open_poly_trade(sig, user_id=chat_id)
                send_telegram_message(
                    enter_msg,
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": [
                        [
                            {"text": "📊 Active Bets (/pstatus)", "callback_data": "/pstatus"},
                            {"text": "💼 Wallet (/pwallet)", "callback_data": "/pwallet"},
                        ]
                    ]},
                )
                return

            if command in {"/pstatus", "/polystatus", "/pbets"}:
                status_text, status_kb = get_poly_status_report(user_id=chat_id)
                send_telegram_message(status_text, chat_id=chat_id, reply_markup=status_kb)
                return

            if command in {"/pclose", "/polyclose", "/pcashout"}:
                if not subcmd:
                    send_telegram_message(
                        "⚠️ Specify the prediction number from <code>/pstatus</code> to cash out, or use <code>/pclose all</code>.\n"
                        "Example: <code>/pclose 1</code>",
                        chat_id=chat_id,
                    )
                    return
                if subcmd.lower() in ("all", "closeall"):
                    _, close_msg = close_all_poly_trades(user_id=chat_id)
                    send_telegram_message(close_msg, chat_id=chat_id)
                    return
                ok, close_msg, _ = close_poly_trade(subcmd, reason="MANUAL_CLOSE", user_id=chat_id)
                send_telegram_message(close_msg, chat_id=chat_id)
                return

            if command == "/preset" or (command in {"/pwallet", "/polywallet"} and subcmd.lower() == "reset"):
                amt_str = parts[2] if (command != "/preset" and len(parts) > 2) else (subcmd if command == "/preset" else "100")
                start_bal = 100.0
                if amt_str and amt_str.lower() != "reset":
                    try:
                        start_bal = float(amt_str.replace("$", "").replace(",", "").strip())
                    except ValueError:
                        send_telegram_message("❌ Invalid balance amount. Example: <code>/pwallet reset 100</code>", chat_id=chat_id)
                        return
                ok, msg = reset_poly_wallet(start_bal, user_id=chat_id)
                send_telegram_message(
                    msg,
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": [[{"text": "🔮 Prediction Menu", "callback_data": "menu_poly"}]]},
                )
                return

            if command in {"/pwallet", "/polywallet", "/pstats"}:
                wallet_text, wallet_kb = get_poly_wallet_report(user_id=chat_id)
                send_telegram_message(wallet_text, chat_id=chat_id, reply_markup=wallet_kb)
                return

        # -------------------------------------------------------------
        # 4. Shared News Command (Accessible to Any Authorized User)
        # -------------------------------------------------------------
        if command in {"/news", "/catalyst", "/catalysts"}:
            is_auth, _ = is_user_authorized(chat_id, user_info)
            if not is_auth and not admin_flag:
                send_telegram_message(UNAUTHORIZED_HELP_TEXT, chat_id=chat_id)
                return
            send_telegram_message("Fetching latest breaking catalysts. One moment...", chat_id=chat_id)
            news_text = format_news_summary(limit=5)
            send_telegram_message(news_text, chat_id=chat_id)
            return

        # -------------------------------------------------------------
        # 5. Crypto & Memecoin Commands (Requires 'crypto' Tier)
        # -------------------------------------------------------------
        crypto_commands = set(COMMANDS.keys()) | {
            "/autopilot", "/autopilot_status", "/autopilot_on", "/autopilot_off",
            "/status", "/trades", "/paper", "/wallet",
            "/summary", "/daily", "/pnl",
            "/history", "/trades_history",
            "/resethistory", "/resetpnl", "/clearhistory", "/clearpnl",
            "/resetaccount", "/resetpaper", "/resetall", "/factoryreset",
            "/setbalance", "/paper_balance",
            "/setsize", "/paper_size", "/lotsize", "/setlot", "/margin",
            "/setleverage", "/leverage", "/lev", "/setlev",
            "/enter", "/paper_enter", "/trade",
            "/close", "/closeall",
        }

        if command in crypto_commands:
            if not _verify_tier_access(chat_id, user_info, "crypto"):
                return

            if command in {"/autopilot", "/autopilot_status", "/autopilot_on", "/autopilot_off"}:
                if command == "/autopilot_on" or subcmd.lower() == "on":
                    autopilot.resume()
                    send_telegram_message(
                        "🟢 <b>Auto-Pilot Activated!</b>\n\n"
                        "Hunting 24/7 for single-pair crypto setups (65% minimum confidence). "
                        "When a setup triggers, you'll receive a direct single-trade alert.",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": [
                            [{"text": "🔴 Pause Auto-Pilot", "callback_data": "/autopilot off"}],
                            [{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}],
                        ]},
                    )
                    return
                elif command == "/autopilot_off" or subcmd.lower() == "off":
                    autopilot.stop()
                    send_telegram_message(
                        "🔴 <b>Auto-Pilot Paused.</b>\n\nBackground scanning is suspended. Send <code>/autopilot on</code> to resume.",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": [
                            [{"text": "🟢 Activate Auto-Pilot", "callback_data": "/autopilot on"}],
                            [{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}],
                        ]},
                    )
                    return
                else:
                    ap_keyboard = [
                        [
                            {"text": "🟢 Turn ON", "callback_data": "/autopilot on"},
                            {"text": "🔴 Turn OFF", "callback_data": "/autopilot off"},
                        ],
                        [
                            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
                        ],
                    ]
                    send_telegram_message(autopilot.get_status_text(), chat_id=chat_id, reply_markup={"inline_keyboard": ap_keyboard})
                    return

            if command in {"/status", "/trades", "/paper", "/wallet"}:
                status_text, status_keyboard = get_status_report(user_id=chat_id)
                send_telegram_message(status_text, chat_id=chat_id, reply_markup=status_keyboard)
                return

            if command in {"/summary", "/daily", "/pnl"}:
                target_date = subcmd if subcmd else None
                summary_text = get_daily_summary_report(target_date, user_id=chat_id)
                send_telegram_message(
                    summary_text,
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": [[{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}]]},
                )
                return

            if command in {"/history", "/trades_history"}:
                send_telegram_message(
                    get_trade_history_report(user_id=chat_id),
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": [[{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}]]},
                )
                return

            if command in {"/resethistory", "/resetpnl", "/clearhistory", "/clearpnl"}:
                ok, msg = reset_trade_history(user_id=chat_id)
                send_telegram_message(
                    msg,
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": [
                        [
                            {"text": "📊 View Status", "callback_data": "/status"},
                            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
                        ]
                    ]},
                )
                return

            if command in {"/resetaccount", "/resetpaper", "/resetall", "/factoryreset"}:
                if not subcmd:
                    reset_kb = [
                        [
                            {"text": "⚠️ Reset ($100)", "callback_data": "/resetaccount 100"},
                            {"text": "⚠️ Reset ($1,000)", "callback_data": "/resetaccount 1000"},
                        ],
                        [
                            {"text": "⚠️ Reset ($10,000)", "callback_data": "/resetaccount confirm"},
                            {"text": "⚠️ Reset ($25,000)", "callback_data": "/resetaccount 25000"},
                        ],
                        [
                            {"text": "❌ Cancel (Crypto Menu)", "callback_data": "menu_crypto"},
                        ],
                    ]
                    send_telegram_message(
                        "⚠️ <b>Confirm Crypto Paper Account Reset?</b>\n\n"
                        "This action will:\n"
                        "• Close and clear all active open crypto trades\n"
                        "• Wipe crypto trade history and PnL statistics\n"
                        "• Reset virtual balance to starting capital\n\n"
                        "Tap a button below or specify custom balance: <code>/resetaccount &lt;amount&gt;</code>",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": reset_kb},
                    )
                    return
                starting_balance = 10000.0
                if subcmd.lower() != "confirm":
                    clean_val = subcmd.replace("$", "").replace(",", "").strip()
                    try:
                        starting_balance = float(clean_val)
                    except ValueError:
                        send_telegram_message("❌ Invalid balance. Usage: <code>/resetaccount [starting_balance]</code> (e.g. <code>/resetaccount 100</code>)", chat_id=chat_id)
                        return
                ok, msg = reset_paper_account(starting_balance, user_id=chat_id)
                send_telegram_message(
                    msg,
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": [
                        [
                            {"text": "📊 Live Status", "callback_data": "/status"},
                            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
                        ]
                    ]},
                )
                return

            if command in {"/setbalance", "/paper_balance"}:
                if not subcmd:
                    settings = get_paper_settings(user_id=chat_id)
                    bal_kb = [
                        [
                            {"text": "$100", "callback_data": "/setbalance 100"},
                            {"text": "$1,000", "callback_data": "/setbalance 1000"},
                            {"text": "$5,000", "callback_data": "/setbalance 5000"},
                        ],
                        [
                            {"text": "$10,000", "callback_data": "/setbalance 10000"},
                            {"text": "$25,000", "callback_data": "/setbalance 25000"},
                            {"text": "$50,000", "callback_data": "/setbalance 50000"},
                        ],
                        [
                            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
                        ],
                    ]
                    send_telegram_message(
                        f"💼 <b>Current Crypto Virtual Balance:</b> <code>${settings['virtual_balance']:,.2f}</code>\n\n"
                        f"Tap a quick-set button below or type: <code>/setbalance &lt;amount&gt;</code>\n"
                        f"Example: <code>/setbalance 100</code>",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": bal_kb},
                    )
                    return
                clean_val = subcmd.replace("$", "").replace(",", "").strip()
                try:
                    val = float(clean_val)
                    ok, msg = set_virtual_balance(val, user_id=chat_id)
                    send_telegram_message(
                        msg,
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": [[{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}]]},
                    )
                except ValueError:
                    send_telegram_message("❌ Invalid balance amount. Example: <code>/setbalance 100</code>", chat_id=chat_id)
                return

            if command in {"/setsize", "/paper_size", "/lotsize", "/setlot", "/margin"}:
                if not subcmd:
                    settings = get_paper_settings(user_id=chat_id)
                    size_kb = [
                        [
                            {"text": "$10", "callback_data": "/setsize 10"},
                            {"text": "$25", "callback_data": "/setsize 25"},
                            {"text": "$50", "callback_data": "/setsize 50"},
                        ],
                        [
                            {"text": "$100", "callback_data": "/setsize 100"},
                            {"text": "$500", "callback_data": "/setsize 500"},
                            {"text": "$1,000", "callback_data": "/setsize 1000"},
                        ],
                        [
                            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
                        ],
                    ]
                    send_telegram_message(
                        f"⚙️ <b>Current Crypto Trade Margin:</b> <code>${settings['trade_size_usd']:,.2f}</code> per entry\n\n"
                        f"Tap a quick-set button below or type: <code>/setsize &lt;amount&gt;</code>\n"
                        f"Example: <code>/setsize 10</code>",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": size_kb},
                    )
                    return
                clean_val = subcmd.replace("$", "").replace(",", "").strip()
                try:
                    val = float(clean_val)
                    ok, msg = set_trade_size(val, user_id=chat_id)
                    send_telegram_message(
                        msg,
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": [[{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}]]},
                    )
                except ValueError:
                    send_telegram_message("❌ Invalid size amount. Example: <code>/setsize 10</code>", chat_id=chat_id)
                return

            if command in {"/setleverage", "/leverage", "/lev", "/setlev"}:
                if not subcmd:
                    settings = get_paper_settings(user_id=chat_id)
                    lev = settings.get("leverage", 1.0)
                    margin = settings.get("trade_size_usd", 1000.0)
                    lev_kb = [
                        [
                            {"text": "1x Spot", "callback_data": "/setleverage 1x"},
                            {"text": "5x", "callback_data": "/setleverage 5x"},
                            {"text": "10x", "callback_data": "/setleverage 10x"},
                        ],
                        [
                            {"text": "20x", "callback_data": "/setleverage 20x"},
                            {"text": "25x", "callback_data": "/setleverage 25x"},
                            {"text": "50x", "callback_data": "/setleverage 50x"},
                        ],
                        [
                            {"text": "75x", "callback_data": "/setleverage 75x"},
                            {"text": "100x", "callback_data": "/setleverage 100x"},
                            {"text": "125x Max", "callback_data": "/setleverage 125x"},
                        ],
                        [
                            {"text": "📈 Crypto Menu", "callback_data": "menu_crypto"},
                        ],
                    ]
                    send_telegram_message(
                        f"⚙️ <b>Current Paper Trading Leverage:</b> <code>{lev:g}x</code>\n"
                        f"• <b>Margin Per Trade:</b> <code>${margin:,.2f}</code>\n"
                        f"• <b>Total Buying Power:</b> <code>${margin * lev:,.2f}</code>\n\n"
                        f"Tap a quick-set button below or type: <code>/setleverage &lt;multiplier&gt;</code>\n"
                        f"<i>(Range: 1x to 125x)</i>",
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": lev_kb},
                    )
                    return
                clean_val = subcmd.lower().replace("x", "").strip()
                try:
                    val = float(clean_val)
                    ok, msg = set_leverage(val, user_id=chat_id)
                    send_telegram_message(
                        msg,
                        chat_id=chat_id,
                        reply_markup={"inline_keyboard": [[{"text": "📈 Crypto Menu", "callback_data": "menu_crypto"}]]},
                    )
                except ValueError:
                    send_telegram_message("❌ Invalid leverage amount. Example: <code>/setleverage 25x</code>", chat_id=chat_id)
                return

            if command in {"/enter", "/paper_enter", "/trade"}:
                if not subcmd:
                    send_telegram_message(
                        "⚠️ Please specify a symbol to enter.\n\n"
                        "Usage: <code>/enter &lt;symbol&gt;</code>\n"
                        "Example: <code>/enter BTC</code> or <code>/enter SOL</code>",
                        chat_id=chat_id,
                    )
                    return
                target_sym = normalize_symbol(subcmd)

                try:
                    candles = fetch_live_candles(target_sym)
                    report = analyze_setup(target_sym, candles, min_confidence=60)
                    action = "BUY" if report.action in ("BUY", "LONG") else ("SELL" if report.action in ("SELL", "SHORT") else "BUY")
                    if report.action != action or not report.stop_loss or not (report.tp1 or report.take_profit):
                        report = compute_trade_levels(target_sym, action, candles)
                    curr_close = float(candles.iloc[-1]["close"])
                    entry = report.entry or curr_close
                    sl = report.stop_loss or (entry * 0.985 if action == "BUY" else entry * 1.015)
                    risk_dist = abs(entry - sl)
                    tp1 = report.tp1 or report.take_profit or (entry + risk_dist if action == "BUY" else entry - risk_dist)
                    order_type = getattr(report, "order_type", "MARKET")
                    ok, enter_msg, _ = open_paper_trade(
                        target_sym, action, entry, sl, tp1, user_id=chat_id,
                        order_type=order_type, current_price=curr_close,
                    )
                    send_telegram_message(enter_msg, chat_id=chat_id)
                except Exception as e:
                    send_telegram_message(f"❌ Failed to enter paper trade for {target_sym}: {e}", chat_id=chat_id)
                return

            if command in {"/close", "/closeall"}:
                if command == "/closeall" or subcmd.lower() in ("all", "closeall"):
                    cnt, close_msg = close_all_open_trades(user_id=chat_id)
                    send_telegram_message(close_msg, chat_id=chat_id)
                    return
                if not subcmd:
                    send_telegram_message(
                        "⚠️ Specify a symbol to close, or use <code>/closeall</code>.\n"
                        "Example: <code>/close BTC</code>",
                        chat_id=chat_id,
                    )
                    return
                target_sym = normalize_symbol(subcmd)
                curr_price = 0.0
                try:
                    candles = fetch_live_candles(target_sym)
                    curr_price = float(candles.iloc[-1]["close"])
                except Exception:
                    pass
                ok, msg, _ = close_paper_trade(target_sym, curr_price if curr_price > 0 else 1.0, reason="MANUAL_CLOSE", user_id=chat_id)
                send_telegram_message(msg, chat_id=chat_id)
                return

            if command in COMMANDS:
                send_telegram_message("Scanning crypto market. One moment...", chat_id=chat_id)
                try:
                    check_open_trades(user_id=chat_id)
                except Exception:
                    pass
                message_text, setups = build_session_scan(COMMANDS[command], min_confidence=65)
                reply_markup = None
                if setups:
                    buttons = []
                    for stp in setups:
                        disp = DISPLAY_NAMES.get(stp.symbol, stp.symbol.replace("_", "/"))
                        btn_text = f"🚀 Enter {disp} ({stp.action})"
                        buttons.append([{"text": btn_text, "callback_data": f"paper_enter:{stp.symbol}:{stp.action}"}])
                    reply_markup = {"inline_keyboard": buttons}
                send_telegram_message(message_text, chat_id=chat_id, reply_markup=reply_markup)
                return

        # Check general auth before saying unknown command
        is_auth, auth_status = is_user_authorized(chat_id, user_info)
        if not is_auth and not admin_flag:
            send_telegram_message(UNAUTHORIZED_HELP_TEXT, chat_id=chat_id)
            return

        send_telegram_message("Unknown command. Send <code>/menu</code> to open the Crypto & Prediction menus.", chat_id=chat_id)
    except Exception as exc:
        print(f"Error handling command '{command}' for chat {chat_id}: {exc}")
        send_telegram_message(f"Error processing {command}: {exc}", chat_id=chat_id)


def main() -> None:
    print("Starting AutoPilot background engine...")
    autopilot.start_background()
    print("Telegram command bot is active and listening for messages...")
    offset = None

    while True:
        payload = {"timeout": 10}
        if offset is not None:
            payload["offset"] = offset

        try:
            try:
                process_due_broadcasts()
            except Exception as e:
                print(f"[ScheduledBroadcast] Loop error: {e}")

            updates = telegram_request("getUpdates", payload, timeout=25).get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback_query(update["callback_query"])

        except Exception as exc:
            print(f"Polling error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
