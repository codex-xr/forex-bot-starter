import os
import requests
from dotenv import load_dotenv


def telegram_request(method: str, payload: dict | None = None, timeout: int | tuple = 15) -> dict:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=payload or {}, timeout=timeout)

    if not response.ok:
        try:
            err_data = response.json()
            description = err_data.get("description", f"HTTP {response.status_code}")
        except Exception:
            description = f"HTTP {response.status_code}"
        raise RuntimeError(f"Telegram API error ({description})")

    return response.json()


MAX_TELEGRAM_TEXT_LENGTH = 4000


def _split_telegram_message(text: str, max_len: int = MAX_TELEGRAM_TEXT_LENGTH) -> list[str]:
    """Splits oversized Telegram messages on clean paragraph/line boundaries under the 4096-char limit."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n\n", 0, max_len)
        if split_at < max_len // 3:
            split_at = remaining.rfind("\n", 0, max_len)
        if split_at < max_len // 3:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at <= 0:
            split_at = max_len

        chunk = remaining[:split_at].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)
    return chunks or [text[:max_len]]


def _resolve_target_chat_ids(chat_id: str | int | None) -> list[str | int]:
    raw_target = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID")
    if raw_target is None or raw_target == "":
        return []
    if isinstance(raw_target, int):
        return [raw_target]
    raw_str = str(raw_target).strip().strip("<>\"'")
    if not raw_str:
        return []
    if "," in raw_str:
        targets: list[str | int] = []
        for part in raw_str.split(","):
            clean = part.strip().strip("<>\"'")
            if clean:
                targets.append(clean)
        return targets
    return [raw_str]


def send_telegram_message(
    message: str,
    chat_id: str | int | None = None,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> None:
    load_dotenv()

    targets = _resolve_target_chat_ids(chat_id)
    if not targets:
        print("Telegram chat is not configured. Skipping message.")
        return

    chunks = _split_telegram_message(str(message))
    for target in targets:
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            payload = {
                "chat_id": target,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup and is_last:
                payload["reply_markup"] = reply_markup

            telegram_request("sendMessage", payload)


def register_telegram_commands() -> dict:
    """
    Registers command lists with Telegram:
    - Default scope (regular users): signal scanning, account, help commands only.
    - Admin scope (admin chat): full admin suite + signal scanning commands.
    """
    user_commands = [
        {"command": "menu", "description": "📱 Main Menu (Crypto & Prediction Menus)"},
        {"command": "poly", "description": "🔮 Scan Top 5 Polymarket Predictions"},
        {"command": "pstatus", "description": "📊 Active Polymarket Bets & Live PnL"},
        {"command": "psummary", "description": "📈 Polymarket End-of-Day PnL Report"},
        {"command": "pwallet", "description": "💼 Polymarket Wallet Balance & Stats"},
        {"command": "polybet", "description": "💵 Set Polymarket Bet Size (/polybet 10)"},
        {"command": "penter", "description": "🎯 Enter Prediction Bet (/penter 1)"},
        {"command": "pclose", "description": "💰 Cash Out Prediction (/pclose 1)"},
        {"command": "c1", "description": "🚀 Major Cryptos (BTC, ETH, SOL, XRP...)"},
        {"command": "c2", "description": "⚡ High-Momentum Altcoins (BNB, SUI...)"},
        {"command": "m1", "description": "🐶 Top Memes (WIF, PEPE, BONK, SHIB)"},
        {"command": "m2", "description": "🐸 Trending Memes (TRUMP, PENGU...)"},
        {"command": "status", "description": "📊 Live Crypto Positions & Floating PnL"},
        {"command": "summary", "description": "📅 Crypto End-of-Day PnL Report"},
        {"command": "enter", "description": "🚀 Enter Crypto Paper Trade (/enter BTC)"},
        {"command": "close", "description": "❌ Close Crypto Paper Trade (/close BTC)"},
        {"command": "history", "description": "📜 Recent Crypto Trade History"},
        {"command": "setbalance", "description": "💼 Set Crypto Balance (/setbalance 100)"},
        {"command": "setsize", "description": "⚙️ Set Crypto Margin (/setsize 10)"},
        {"command": "setleverage", "description": "⚡ Set Crypto Leverage (/setleverage 25x)"},
        {"command": "news", "description": "📰 Breaking Catalysts & News Monitor"},
        {"command": "time", "description": "⏰ Recommended Daily Scan Times"},
        {"command": "myplan", "description": "ℹ️ View VIP Subscription Status"},
        {"command": "redeem", "description": "🔑 Activate VIP Key (/redeem KEY)"},
        {"command": "help", "description": "❓ Show Full Help & Command Guide"},
    ]

    admin_commands = [
        {"command": "menu", "description": "📱 Main Menu (Crypto & Prediction Menus)"},
        {"command": "admin", "description": "👑 Master Admin Control Panel"},
        {"command": "poly", "description": "🔮 Scan Top 5 Polymarket Predictions"},
        {"command": "pstatus", "description": "📊 Active Polymarket Bets & Live PnL"},
        {"command": "psummary", "description": "📈 Polymarket End-of-Day PnL Report"},
        {"command": "pwallet", "description": "💼 Polymarket Wallet Balance & Stats"},
        {"command": "polybet", "description": "💵 Set Polymarket Bet Size (/polybet 10)"},
        {"command": "c1", "description": "🚀 Major Cryptos Scan"},
        {"command": "c2", "description": "⚡ Hot Altcoins Scan"},
        {"command": "m1", "description": "🐶 Top Memes Scan"},
        {"command": "m2", "description": "🐸 Trending Memes Scan"},
        {"command": "status", "description": "📊 Live Crypto Trades & Floating PnL"},
        {"command": "summary", "description": "📅 Crypto End-of-Day PnL Report"},
        {"command": "enter", "description": "🚀 Enter Crypto Paper Trade (/enter BTC)"},
        {"command": "close", "description": "❌ Close Crypto Paper Trade (/close BTC)"},
        {"command": "history", "description": "📜 Recent Crypto Trade History"},
        {"command": "autopilot", "description": "🤖 Auto-Pilot 24/7 Live Calls (/autopilot)"},
        {"command": "news", "description": "📰 Breaking Catalysts & News Monitor"},
        {"command": "time", "description": "⏰ Recommended Daily Scan Times"},
        {"command": "users", "description": "👥 User Dashboard & Access Tracker"},
        {"command": "genkey", "description": "🔑 Generate Key (/genkey 30d crypto|poly|both)"},
        {"command": "grant", "description": "🎁 Direct VIP Grant (/grant id 30d poly)"},
        {"command": "revoke", "description": "🚫 Revoke/Ban User Access"},
        {"command": "unban", "description": "✅ Restore/Unban User Access"},
        {"command": "keys", "description": "🗝️ View Available & Redeemed Keys"},
        {"command": "broadcast", "description": "📢 Instant Announcement to All"},
        {"command": "schedule", "description": "⏰ Schedule Broadcast Alert"},
        {"command": "schedules", "description": "📋 View Active Schedules"},
        {"command": "help", "description": "❓ Show Full Help & Command Guide"},
    ]

    res = {}
    try:
        res["default"] = telegram_request("setMyCommands", {"commands": user_commands, "scope": {"type": "default"}})
    except Exception as exc:
        res["default_error"] = str(exc)

    admin_env = os.getenv("TELEGRAM_CHAT_ID", "6686703329").strip().strip("<>\"'")
    for aid in admin_env.split(","):
        clean_aid = aid.strip()
        if clean_aid.isdigit():
            try:
                res[f"admin_{clean_aid}"] = telegram_request(
                    "setMyCommands",
                    {"commands": admin_commands, "scope": {"type": "chat", "chat_id": int(clean_aid)}},
                )
            except Exception as exc:
                res[f"admin_{clean_aid}_error"] = str(exc)

    return res


