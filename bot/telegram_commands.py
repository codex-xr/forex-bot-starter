import html
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.access_control import (
    is_admin,
    is_user_authorized,
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
)
from bot.session_bot import build_session_scan, build_session_message, ALL_WATCHLIST
from bot.signal_engine import analyze_setup
from bot.symbols import DISPLAY_NAMES, normalize_symbol
from bot.telegram import send_telegram_message, telegram_request


COMMANDS = {
    "/f1": "f1",
    "/f2": "f2",
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


def get_menu_keyboard(is_admin_user: bool = False) -> dict:
    """Builds interactive Telegram inline buttons for instant 1-tap commands."""
    keyboard = [
        [
            {"text": "📈 Forex Majors (/f1)", "callback_data": "/f1"},
            {"text": "🏆 Gold & Crosses (/f2)", "callback_data": "/f2"},
        ],
        [
            {"text": "🚀 Major Cryptos (/c1)", "callback_data": "/c1"},
            {"text": "⚡ Hot Altcoins (/c2)", "callback_data": "/c2"},
        ],
        [
            {"text": "🐶 Top Memes (/m1)", "callback_data": "/m1"},
            {"text": "🐸 Trending Memes (/m2)", "callback_data": "/m2"},
        ],
        [
            {"text": "📊 Live Trades (/status)", "callback_data": "/status"},
            {"text": "📅 Daily PnL (/summary)", "callback_data": "/summary"},
        ],
        [
            {"text": "📰 Breaking News", "callback_data": "/news"},
            {"text": "🤖 Auto-Pilot", "callback_data": "/autopilot"},
        ],
        [
            {"text": "ℹ️ My Subscription", "callback_data": "/myplan"},
            {"text": "📜 Trade History", "callback_data": "/history"},
        ],
    ]
    if is_admin_user:
        keyboard.append([
            {"text": "👑 Master Admin Panel (/admin)", "callback_data": "/admin"},
        ])
        keyboard.append([
            {"text": "👥 User Dashboard", "callback_data": "/users"},
            {"text": "🔑 VIP Keys", "callback_data": "/keys"},
        ])
    else:
        keyboard.append([
            {"text": "❓ Full Help Menu", "callback_data": "/help"},
        ])
    return {"inline_keyboard": keyboard}


def get_admin_keyboard() -> dict:
    """Dedicated Admin Control Panel buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "👥 User Dashboard (/users)", "callback_data": "/users"},
                {"text": "🔑 VIP Keys (/keys)", "callback_data": "/keys"},
            ],
            [
                {"text": "📊 Live Trades (/status)", "callback_data": "/status"},
                {"text": "📅 Daily PnL (/summary)", "callback_data": "/summary"},
            ],
            [
                {"text": "⏰ Active Schedules (/schedules)", "callback_data": "/schedules"},
                {"text": "🤖 Auto-Pilot (/autopilot)", "callback_data": "/autopilot"},
            ],
            [
                {"text": "📈 Forex Scan (/f1)", "callback_data": "/f1"},
                {"text": "🚀 Crypto Scan (/c1)", "callback_data": "/c1"},
            ],
            [
                {"text": "🐶 Meme Scan (/m1)", "callback_data": "/m1"},
                {"text": "📰 Breaking News (/news)", "callback_data": "/news"},
            ],
            [
                {"text": "📱 User Menu (/menu)", "callback_data": "/menu"},
                {"text": "❓ Help Guide (/help)", "callback_data": "/help"},
            ],
        ]
    }


USER_HELP_TEXT = """🔥 <b>Forex, Crypto & Memecoin Signal Dashboard</b>

Tap any button below or type a command to scan the market:

📈 <b>Forex & Commodities:</b>
• <code>/f1</code> — Forex Majors (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD)
• <code>/f2</code> — Crosses & Gold (NZDUSD, EURGBP, EURJPY, GBPJPY, Gold, US30)

🚀 <b>Crypto Market Scans:</b>
• <code>/c1</code> — Major Cryptos (BTC, ETH, SOL, XRP, DOGE, ADA)
• <code>/c2</code> — High-Momentum Altcoins (BNB, AVAX, LINK, SUI, NEAR, LTC)

🐶🐸 <b>High-Volatility Memecoin Scans:</b>
• <code>/m1</code> — Top Memes (WIF, PEPE, SHIB, BONK, FLOKI, BRETT, ANSEM)
• <code>/m2</code> — Trending Memes (TRUMP, BOME, PENGU, MOG, PEOPLE, ELON)

💼 <b>Paper Trading & Forward-Testing:</b>
• <code>/status</code> — Active open trades, live prices & floating PnL
• <code>/summary</code> — End-of-day performance, daily PnL, best/worst pairs
• <code>/setbalance &lt;amt&gt;</code> — Set virtual account capital (e.g. <code>/setbalance 10000</code>)
• <code>/setsize &lt;amt&gt;</code> — Set entry margin size per trade (e.g. <code>/setsize 500</code>)
• <code>/setleverage &lt;multiplier&gt;</code> — Set trade leverage (e.g. <code>/setleverage 10x</code>, <code>/setleverage 20x</code>)
• <code>/enter &lt;symbol&gt;</code> — Enter a paper trade manually (e.g. <code>/enter BTC</code>)
• <code>/close &lt;symbol&gt;</code> — Close an active paper trade (or <code>/closeall</code>)
• <code>/history</code> — View recent closed trade history

📰 <b>Breaking News & Catalysts:</b>
• <code>/news</code> — Real-time Crypto, Trump & Macro Sentiment Monitor

🤖 <b>Auto-Pilot 24/7 Alerts:</b>
• <code>/autopilot</code> — View Auto-Pilot status
• <code>/autopilot on</code> — Activate 24/7 background signal hunting
• <code>/autopilot off</code> — Pause background monitoring

ℹ️ <b>Account & Setup:</b>
• <code>/menu</code> — Show Interactive Button Menu
• <code>/myplan</code> — View your VIP subscription status
• <code>/help</code> — Show this full guide
"""

ADMIN_HELP_TEXT = USER_HELP_TEXT + """
👑 <b>Master Admin Control Panel:</b>
• <code>/admin</code> — Open Admin Interactive Dashboard
• <code>/genkey &lt;duration&gt;</code> — Generate VIP Key (e.g. <code>/genkey 30d</code>, <code>/genkey lifetime</code>)
• <code>/users</code> — View all registered users, visitors & stats
• <code>/revoke &lt;user_id&gt;</code> — Terminate/ban a user's access
• <code>/unban &lt;user_id&gt;</code> — Restore an account / unban a user
• <code>/grant &lt;user_id&gt; &lt;duration&gt;</code> — Directly grant access without key
• <code>/keys</code> — View available & redeemed keys
• <code>/broadcast &lt;message&gt;</code> — Send instant announcement to all users
• <code>/schedule &lt;timing&gt; &lt;message&gt;</code> — Schedule one-time or recurring broadcast
• <code>/schedules</code> — View all active scheduled broadcasts
• <code>/cancelschedule &lt;id&gt;</code> — Cancel a scheduled broadcast
"""

UNAUTHORIZED_HELP_TEXT = """🔒 <b>Forex, Crypto & Memecoin Signal Bot</b>

<i>This is a private VIP signal system. Access is restricted to authorized accounts only.</i>

🔑 <b>Have an Activation Key?</b>
Activate your account instantly with:
👉 <code>/redeem &lt;YOUR_KEY&gt;</code>
<i>(Example: <code>/redeem VIP-8842-30D</code>)</i>

ℹ️ <b>Available Commands:</b>
• <code>/redeem &lt;KEY&gt;</code> — Activate your VIP access
• <code>/myplan</code> — Check subscription status
• <code>/help</code> — Show this message

<i>To obtain an activation key, please contact the Administrator.</i>
"""


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

    # 1. Handle 1-Click Paper Trade Entry
    if data.startswith("paper_enter:"):
        parts = data.split(":")
        if len(parts) >= 3:
            symbol = normalize_symbol(parts[1])
            action = parts[2]
            try:
                candles = fetch_live_candles(symbol)
                report = analyze_setup(symbol, candles, min_confidence=50)
                curr_close = float(candles.iloc[-1]["close"])
                entry = report.entry or curr_close
                sl = report.stop_loss or (entry * 0.98 if action == "BUY" else entry * 1.02)
                tp1 = report.tp1 or report.take_profit or (entry * 1.03 if action == "BUY" else entry * 0.97)
                ok, enter_msg, _ = open_paper_trade(symbol, action, entry, sl, tp1)
                send_telegram_message(enter_msg, chat_id=chat_id)
            except Exception as e:
                send_telegram_message(f"❌ Failed to enter paper trade for {symbol}: {e}", chat_id=chat_id)
        return

    # 2. Handle Manual Paper Trade Close from Status Dashboard
    if data.startswith("paper_close:"):
        pos_id = data.split(":", 1)[1]
        try:
            from bot.paper_engine import _load_paper_store
            store = _load_paper_store()
            pos = store.positions.get(pos_id)
            curr_price = pos.entry_price if pos else 0.0
            if pos:
                try:
                    df = fetch_live_candles(pos.symbol)
                    if not df.empty:
                        curr_price = float(df.iloc[-1]["close"])
                except Exception:
                    pass
            ok, close_msg, _ = close_paper_trade(pos_id, curr_price if curr_price > 0 else 1.0, reason="MANUAL_CLOSE")
            send_telegram_message(close_msg, chat_id=chat_id)
        except Exception as e:
            send_telegram_message(f"❌ Failed to close position: {e}", chat_id=chat_id)
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
        # -------------------------------------------------------------
        # 1. Public Account & Help Commands (Always Accessible)
        # -------------------------------------------------------------
        if command in {"/start", "/help", "/menu", "/commands", "/cmds", "/dashboard", "/dash", "/signals"}:
            is_auth, _ = is_user_authorized(chat_id, user_info)
            admin_flag = is_admin(chat_id) or (user_info and is_admin(user_info.get("id")))
            if admin_flag:
                send_telegram_message(
                    ADMIN_HELP_TEXT,
                    chat_id=chat_id,
                    reply_markup=get_menu_keyboard(is_admin_user=True),
                )
            elif is_auth:
                send_telegram_message(
                    USER_HELP_TEXT,
                    chat_id=chat_id,
                    reply_markup=get_menu_keyboard(is_admin_user=False),
                )
            else:
                send_telegram_message(UNAUTHORIZED_HELP_TEXT, chat_id=chat_id)
            return

        if command == "/redeem":
            if not subcmd:
                send_telegram_message(
                    "⚠️ Please provide an activation key.\n\nUsage: <code>/redeem &lt;YOUR_KEY&gt;</code>\nExample: <code>/redeem VIP-A1B2-30D</code>",
                    chat_id=chat_id,
                )
                return
            ok, msg = redeem_key(chat_id, user_info, subcmd)
            send_telegram_message(msg, chat_id=chat_id)
            return

        if command in {"/myplan", "/account", "/plan"}:
            send_telegram_message(get_user_plan_report(chat_id), chat_id=chat_id)
            return

        # -------------------------------------------------------------
        # 2. Admin Only Commands
        # -------------------------------------------------------------
        if command in {
            "/admin", "/genkey", "/users", "/subscribers", "/members", "/revoke", "/ban",
            "/unban", "/restore", "/grant", "/keys", "/broadcast",
            "/schedule", "/schedules", "/cancelschedule", "/delschedule", "/unschedule"
        }:
            admin_flag = is_admin(chat_id) or (user_info and is_admin(user_info.get("id")))
            if not admin_flag:
                send_telegram_message("🚫 <b>Access Denied:</b> This command is reserved for the Master Administrator.", chat_id=chat_id)
                return

            if command == "/admin":
                admin_msg = (
                    "👑 <b>Master Administrator Control Panel</b>\n\n"
                    "• <b>Status:</b> 🟢 Online (Cloud Database Connected)\n"
                    "• <b>Access:</b> Full Admin Authorization\n\n"
                    "<b>Admin Controls:</b>\n"
                    "• <code>/users</code> — View all registered users & visitors\n"
                    "• <code>/genkey &lt;dur&gt;</code> — Generate VIP Key (e.g. <code>/genkey 30d</code>)\n"
                    "• <code>/grant &lt;id&gt; &lt;dur&gt;</code> — Direct VIP Grant\n"
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
                note = " ".join(parts[2:]) if len(parts) > 2 else ""
                key_code, label = generate_key(dur, note)
                msg = (
                    f"🔑 <b>New VIP Activation Key Generated!</b>\n\n"
                    f"• <b>Key:</b> <code>{key_code}</code>\n"
                    f"• <b>Duration:</b> <b>{label}</b>\n\n"
                    f"<i>Send this key to your user. They can activate it with:</i>\n"
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
                    send_telegram_message("⚠️ Usage: <code>/grant &lt;user_id or @username&gt; [duration]</code>\nExample: <code>/grant 123456789 30d</code>", chat_id=chat_id)
                    return
                dur = parts[2] if len(parts) > 2 else "30d"
                ok, msg = grant_user(subcmd, dur)
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
                        "• One-time: <code>/schedule in 2h London Open alert!</code>\n"
                        "• Recurring: <code>/schedule every 24h Daily market check</code>\n"
                        "• Daily UTC: <code>/schedule daily 08:30 Good morning traders!</code>",
                        chat_id=chat_id,
                    )
                    return

                # Parse timing and message body
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
        # 3. Gatekeeper: Verify Authorization for All Trading Commands
        # -------------------------------------------------------------
        is_auth, auth_status = is_user_authorized(chat_id, user_info)
        if not is_auth:
            if auth_status == "revoked":
                send_telegram_message("🚫 <b>Access Revoked:</b> Your access to this bot has been terminated by the Administrator.", chat_id=chat_id)
            elif auth_status == "expired":
                send_telegram_message("⏳ <b>Subscription Expired:</b> Your VIP access has expired. Please redeem a new key with <code>/redeem &lt;KEY&gt;</code> or contact the Admin.", chat_id=chat_id)
            else:
                send_telegram_message(
                    "🔒 <b>Access Restricted</b>\n\n"
                    "You need an active VIP activation key to use this bot.\n\n"
                    "To activate access, use:\n"
                    "👉 <code>/redeem &lt;YOUR_KEY&gt;</code>\n\n"
                    "<i>To obtain a key, please contact the Administrator.</i>",
                    chat_id=chat_id,
                )
            return

        # -------------------------------------------------------------
        # 4. Authorized VIP Feature Execution
        # -------------------------------------------------------------
        if command in {"/news", "/catalyst", "/catalysts"}:
            send_telegram_message("Fetching latest breaking catalysts. One moment...", chat_id=chat_id)
            news_text = format_news_summary(limit=5)
            send_telegram_message(news_text, chat_id=chat_id)
            return

        if command in {"/autopilot", "/autopilot_status", "/autopilot_on", "/autopilot_off"}:
            if command == "/autopilot_on" or subcmd.lower() == "on":
                autopilot.resume()
                send_telegram_message(
                    "🟢 <b>Auto-Pilot Activated!</b>\n\n"
                    "Hunting 24/7 for single-pair trade setups (65% minimum confidence). "
                    "When a setup triggers, you'll receive a direct single-trade alert.",
                    chat_id=chat_id,
                )
                return
            elif command == "/autopilot_off" or subcmd.lower() == "off":
                autopilot.stop()
                send_telegram_message(
                    "🔴 <b>Auto-Pilot Paused.</b>\n\nBackground scanning is suspended. Send <code>/autopilot on</code> to resume.",
                    chat_id=chat_id,
                )
                return
            else:
                send_telegram_message(autopilot.get_status_text(), chat_id=chat_id)
                return

        # -------------------------------------------------------------
        # Paper Trading & Forward-Testing Execution
        # -------------------------------------------------------------
        if command in {"/status", "/trades", "/paper"}:
            status_text, status_keyboard = get_status_report()
            send_telegram_message(status_text, chat_id=chat_id, reply_markup=status_keyboard)
            return

        if command in {"/summary", "/daily", "/pnl"}:
            target_date = subcmd if subcmd else None
            summary_text = get_daily_summary_report(target_date)
            send_telegram_message(summary_text, chat_id=chat_id)
            return

        if command in {"/history", "/trades_history"}:
            send_telegram_message(get_trade_history_report(), chat_id=chat_id)
            return

        if command in {"/setbalance", "/paper_balance"}:
            if not subcmd:
                settings = get_paper_settings()
                send_telegram_message(
                    f"💼 <b>Current Virtual Balance:</b> <code>${settings['virtual_balance']:,.2f}</code>\n\n"
                    f"To update, send: <code>/setbalance &lt;amount&gt;</code>\n"
                    f"Example: <code>/setbalance 10000</code>",
                    chat_id=chat_id,
                )
                return
            clean_val = subcmd.replace("$", "").replace(",", "").strip()
            try:
                val = float(clean_val)
                ok, msg = set_virtual_balance(val)
                send_telegram_message(msg, chat_id=chat_id)
            except ValueError:
                send_telegram_message("❌ Invalid balance amount. Example: <code>/setbalance 10000</code>", chat_id=chat_id)
            return

        if command in {"/setsize", "/paper_size", "/lotsize", "/setlot"}:
            if not subcmd:
                settings = get_paper_settings()
                send_telegram_message(
                    f"⚙️ <b>Current Trade Size:</b> <code>${settings['trade_size_usd']:,.2f}</code> per entry\n\n"
                    f"To update, send: <code>/setsize &lt;amount&gt;</code>\n"
                    f"Example: <code>/setsize 500</code>",
                    chat_id=chat_id,
                )
                return
            clean_val = subcmd.replace("$", "").replace(",", "").strip()
            try:
                val = float(clean_val)
                ok, msg = set_trade_size(val)
                send_telegram_message(msg, chat_id=chat_id)
            except ValueError:
                send_telegram_message("❌ Invalid size amount. Example: <code>/setsize 500</code>", chat_id=chat_id)
            return

        if command in {"/setleverage", "/leverage", "/lev", "/setlev"}:
            if not subcmd:
                settings = get_paper_settings()
                lev = settings.get("leverage", 1.0)
                margin = settings.get("trade_size_usd", 1000.0)
                send_telegram_message(
                    f"⚙️ <b>Current Paper Trading Leverage:</b> <code>{lev:g}x</code>\n"
                    f"• <b>Margin Per Trade:</b> <code>${margin:,.2f}</code>\n"
                    f"• <b>Total Buying Power:</b> <code>${margin * lev:,.2f}</code>\n\n"
                    f"To update leverage, send: <code>/setleverage &lt;multiplier&gt;</code>\n"
                    f"Examples:\n"
                    f"• <code>/setleverage 10x</code> (10x Leverage)\n"
                    f"• <code>/setleverage 20x</code> (20x Leverage)\n"
                    f"• <code>/setleverage 50x</code> (50x Leverage)\n"
                    f"• <code>/setleverage 1x</code> (1x Spot)",
                    chat_id=chat_id,
                )
                return
            clean_val = subcmd.lower().replace("x", "").strip()
            try:
                val = float(clean_val)
                ok, msg = set_leverage(val)
                send_telegram_message(msg, chat_id=chat_id)
            except ValueError:
                send_telegram_message("❌ Invalid leverage amount. Example: <code>/setleverage 10x</code>", chat_id=chat_id)
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
                report = analyze_setup(target_sym, candles, min_confidence=50)
                action = "BUY" if report.action in ("BUY", "LONG") else ("SELL" if report.action in ("SELL", "SHORT") else "BUY")
                curr_price = float(candles.iloc[-1]["close"])
                entry = report.entry or curr_price
                sl = report.stop_loss or (entry * 0.98 if action == "BUY" else entry * 1.02)
                tp1 = report.tp1 or report.take_profit or (entry * 1.03 if action == "BUY" else entry * 0.97)
                ok, enter_msg, _ = open_paper_trade(target_sym, action, entry, sl, tp1)
                send_telegram_message(enter_msg, chat_id=chat_id)
            except Exception as e:
                send_telegram_message(f"❌ Failed to enter paper trade for {target_sym}: {e}", chat_id=chat_id)
            return

        if command in {"/close", "/closeall"}:
            if command == "/closeall" or subcmd.lower() in ("all", "closeall"):
                cnt, close_msg = close_all_open_trades()
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
            ok, msg, _ = close_paper_trade(target_sym, curr_price if curr_price > 0 else 1.0, reason="MANUAL_CLOSE")
            send_telegram_message(msg, chat_id=chat_id)
            return

        if command in COMMANDS:
            send_telegram_message("Scanning market. One moment...", chat_id=chat_id)
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

        send_telegram_message("Unknown command. Send /help to see available commands.", chat_id=chat_id)
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
            # Process due broadcasts in background polling loop
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
