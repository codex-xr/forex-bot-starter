import os
import json
import secrets
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "6686703329"))

# In-memory cache
_STORE: dict = {
    "keys": {},
    "users": {},
}


def _get_store_path() -> Path:
    # On serverless (Vercel/AWS Lambda), root is read-only; use /tmp
    try:
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.touch()
        test_file.unlink()
        return data_dir / "access_store.json"
    except (OSError, PermissionError):
        tmp_dir = Path("/tmp/forex_bot_data")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir / "access_store.json"


def _load_store() -> dict:
    global _STORE
    try:
        path = _get_store_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                _STORE["keys"] = data.get("keys", {})
                _STORE["users"] = data.get("users", {})
    except Exception as exc:
        print(f"[AccessControl] Error loading store: {exc}")
    return _STORE


def _save_store() -> None:
    try:
        path = _get_store_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_STORE, f, indent=2)
    except Exception as exc:
        print(f"[AccessControl] Error saving store: {exc}")


try:
    _load_store()
except Exception:
    pass


def is_admin(chat_id: int | str) -> bool:
    try:
        return int(chat_id) == ADMIN_CHAT_ID
    except (ValueError, TypeError):
        return False


def parse_duration(duration_str: str) -> tuple[int | None, str]:
    """
    Parses duration string like '7d', '30d', '90d', '365d', 'lifetime'.
    Returns (days, label).
    """
    s = duration_str.strip().lower()
    if s in ("lifetime", "perm", "permanent", "unlimited"):
        return None, "Lifetime"
    if s.endswith("d") or s.endswith("days") or s.endswith("day"):
        num_str = "".join(filter(str.isdigit, s))
        days = int(num_str) if num_str else 30
        return days, f"{days} Days"
    if s.endswith("m") or s.endswith("months") or s.endswith("month"):
        num_str = "".join(filter(str.isdigit, s))
        months = int(num_str) if num_str else 1
        days = months * 30
        return days, f"{months} Month{'s' if months > 1 else ''}"
    if s.isdigit():
        days = int(s)
        return days, f"{days} Days"
    return 30, "30 Days"


def generate_key(duration_str: str = "30d", note: str = "") -> tuple[str, str]:
    """
    Generates a unique VIP key and saves it to the store.
    Returns (key_code, duration_label).
    """
    days, label = parse_duration(duration_str)
    random_part = secrets.token_hex(3).upper()
    dur_tag = f"{days}D" if days else "LIFE"
    key_code = f"VIP-{random_part}-{dur_tag}"

    _STORE["keys"][key_code] = {
        "key": key_code,
        "days": days,
        "label": label,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "note": note,
        "status": "unused",
        "used_by": None,
        "used_at": None,
    }
    _save_store()
    return key_code, label


def redeem_key(chat_id: int | str, user_info: dict, key_code: str) -> tuple[bool, str]:
    """
    Redeems a VIP key for a given user.
    """
    _load_store()
    chat_id_str = str(chat_id)
    key_clean = key_code.strip().upper()

    if key_clean not in _STORE["keys"]:
        return False, "❌ Invalid Activation Key. Please double check the code."

    key_data = _STORE["keys"][key_clean]
    if key_data["status"] != "unused":
        used_date = key_data.get("used_at", "earlier")
        return False, f"❌ This Activation Key has already been redeemed on {used_date}."

    now = datetime.now(timezone.utc)
    days = key_data["days"]

    if days is None:
        expires_at_str = "Never (Lifetime)"
        expires_ts = None
    else:
        existing = _STORE["users"].get(chat_id_str)
        if existing and existing.get("status") == "active" and existing.get("expires_ts"):
            current_exp = datetime.utcfromtimestamp(existing["expires_ts"])
            base_date = max(now, current_exp)
        else:
            base_date = now

        exp_date = base_date + timedelta(days=days)
        expires_at_str = exp_date.strftime("%Y-%m-%d %H:%M:%S UTC")
        expires_ts = exp_date.timestamp()

    # Mark key as used
    key_data["status"] = "redeemed"
    key_data["used_by"] = chat_id_str
    key_data["used_at"] = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Update or create user
    _STORE["users"][chat_id_str] = {
        "chat_id": chat_id_str,
        "username": user_info.get("username", "Unknown"),
        "first_name": user_info.get("first_name", "User"),
        "plan_label": key_data["label"],
        "activated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "expires_at": expires_at_str,
        "expires_ts": expires_ts,
        "status": "active",
        "key_used": key_clean,
        "last_active": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    _save_store()

    return True, f"🎉 <b>Access Activated Successfully!</b>\n\n• <b>Plan:</b> {key_data['label']}\n• <b>Expires:</b> <code>{expires_at_str}</code>\n\nYou now have full access to all Forex, Crypto & Memecoin signals! Type /help to begin."


def is_user_authorized(chat_id: int | str, user_info: dict | None = None) -> tuple[bool, str]:
    """
    Checks if a user is authorized to use the bot.
    Returns (is_authorized, reason_message).
    """
    if is_admin(chat_id):
        return True, "Admin"

    _load_store()
    chat_id_str = str(chat_id)
    user = _STORE["users"].get(chat_id_str)

    if not user:
        return False, "unregistered"

    if user_info:
        user["last_active"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if user_info.get("username"):
            user["username"] = user_info["username"]
        _save_store()

    if user.get("status") == "revoked":
        return False, "revoked"

    expires_ts = user.get("expires_ts")
    if expires_ts is not None:
        if time.time() > expires_ts:
            user["status"] = "expired"
            _save_store()
            return False, "expired"

    return True, "active"


def revoke_user(target: str) -> tuple[bool, str]:
    """
    Revokes access for a user by Chat ID or @username.
    """
    _load_store()
    target_clean = target.strip().lstrip("@").lower()

    found_id = None
    for cid, u in _STORE["users"].items():
        if cid == target_clean or (u.get("username") and u.get("username").lower() == target_clean):
            found_id = cid
            break

    if not found_id:
        return False, f"User '{target}' not found in the database."

    user = _STORE["users"][found_id]
    user["status"] = "revoked"
    user["revoked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _save_store()

    uname = f"@{user.get('username')}" if user.get("username") != "Unknown" else user.get("first_name", found_id)
    return True, f"🚫 Access for <b>{uname}</b> (ID: <code>{found_id}</code>) has been <b>REVOKED</b>."


def grant_user(target: str, duration_str: str = "30d", admin_note: str = "Admin Grant") -> tuple[bool, str]:
    """
    Directly grants or extends access for a user without needing a key.
    """
    _load_store()
    target_clean = target.strip().lstrip("@")
    days, label = parse_duration(duration_str)

    now = datetime.now(timezone.utc)
    if days is None:
        expires_at_str = "Never (Lifetime)"
        expires_ts = None
    else:
        exp_date = now + timedelta(days=days)
        expires_at_str = exp_date.strftime("%Y-%m-%d %H:%M:%S UTC")
        expires_ts = exp_date.timestamp()

    found_id = target_clean if target_clean.isdigit() else None
    if not found_id:
        for cid, u in _STORE["users"].items():
            if u.get("username") and u.get("username").lower() == target_clean.lower():
                found_id = cid
                break

    if not found_id:
        found_id = target_clean

    _STORE["users"][found_id] = {
        "chat_id": found_id,
        "username": _STORE["users"].get(found_id, {}).get("username", "Unknown"),
        "first_name": _STORE["users"].get(found_id, {}).get("first_name", "Granted User"),
        "plan_label": label,
        "activated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "expires_at": expires_at_str,
        "expires_ts": expires_ts,
        "status": "active",
        "key_used": admin_note,
        "last_active": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    _save_store()
    return True, f"✅ Access granted to ID <code>{found_id}</code> ({label}, expires: <code>{expires_at_str}</code>)."


def list_users_report() -> str:
    """
    Formats clean Telegram report of all users for the Admin.
    """
    _load_store()
    users = _STORE["users"]
    if not users:
        return "👥 <b>Registered Users:</b>\n\n<i>No users have redeemed access keys yet.</i>"

    active_count = sum(1 for u in users.values() if u.get("status") == "active" and (not u.get("expires_ts") or u.get("expires_ts") > time.time()))
    revoked_count = sum(1 for u in users.values() if u.get("status") == "revoked")
    expired_count = sum(1 for u in users.values() if u.get("status") == "expired" or (u.get("status") == "active" and u.get("expires_ts") and u.get("expires_ts") <= time.time()))

    lines = [
        "👥 <b>VIP User Management & Statistics</b>",
        f"• <b>Total Registered:</b> <code>{len(users)}</code>",
        f"• 🟢 <b>Active:</b> <code>{active_count}</code> | 🔴 <b>Revoked:</b> <code>{revoked_count}</code> | ⏳ <b>Expired:</b> <code>{expired_count}</code>",
        "",
        "<b>User List:</b>",
    ]

    for i, (cid, u) in enumerate(users.items(), 1):
        uname = f"@{u.get('username')}" if u.get("username") and u.get("username") != "Unknown" else u.get("first_name", "User")
        status = u.get("status", "unknown")

        if status == "active" and u.get("expires_ts") and u.get("expires_ts") <= time.time():
            status = "expired"

        emoji = "🟢" if status == "active" else ("🚫" if status == "revoked" else "⏳")
        exp = u.get("expires_at", "N/A")
        plan = u.get("plan_label", "VIP")
        last = u.get("last_active", "Never")

        lines.append(f"{i}. {emoji} <b>{uname}</b> (<code>{cid}</code>)")
        lines.append(f"   • Plan: <code>{plan}</code> | Status: <b>{status.upper()}</b>")
        lines.append(f"   • Expires: <code>{exp}</code>")
        lines.append(f"   • Last active: {last}")
        lines.append("")

    lines.append("<i>To revoke access: <code>/revoke &lt;user_id&gt;</code></i>")
    return "\n".join(lines).strip()


def list_keys_report() -> str:
    """
    Formats report of all generated keys for the Admin.
    """
    _load_store()
    keys = _STORE["keys"]
    if not keys:
        return "🔑 <b>VIP Activation Keys:</b>\n\n<i>No keys generated yet. Use <code>/genkey &lt;duration&gt;</code> to create one.</i>"

    unused = [k for k in keys.values() if k.get("status") == "unused"]
    redeemed = [k for k in keys.values() if k.get("status") == "redeemed"]

    lines = [
        "🔑 <b>VIP Activation Keys</b>",
        f"• <b>Available (Unused):</b> <code>{len(unused)}</code>",
        f"• <b>Redeemed:</b> <code>{len(redeemed)}</code>",
        "",
    ]

    if unused:
        lines.append("<b>Available Keys (Ready to distribute):</b>")
        for k in unused[-10:]:
            note_str = f" ({k['note']})" if k.get("note") else ""
            lines.append(f"• <code>{k['key']}</code> — <b>{k['label']}</b>{note_str}")
        lines.append("")

    if redeemed:
        lines.append("<b>Recently Redeemed:</b>")
        for k in redeemed[-5:]:
            lines.append(f"• <code>{k['key']}</code> — Used by <code>{k.get('used_by')}</code> on {k.get('used_at')}")

    return "\n".join(lines).strip()


def get_user_plan_report(chat_id: int | str) -> str:
    """
    Formats plan status for a user viewing /myplan.
    """
    if is_admin(chat_id):
        return (
            "👑 <b>Account Status: Master Administrator</b>\n\n"
            "• <b>Privilege:</b> Permanent Full Access\n"
            "• <b>Admin Controls:</b> <code>/genkey</code>, <code>/users</code>, <code>/revoke</code>, <code>/grant</code>, <code>/keys</code>, <code>/broadcast</code>"
        )

    _load_store()
    chat_id_str = str(chat_id)
    user = _STORE["users"].get(chat_id_str)

    if not user:
        return (
            "🔒 <b>No Active Subscription</b>\n\n"
            "You do not have an active VIP access key.\n"
            "To activate your account, use: <code>/redeem &lt;YOUR_KEY&gt;</code>\n"
            "Contact the Admin to obtain an activation key."
        )

    status = user.get("status", "unknown")
    if status == "revoked":
        return "🚫 <b>Account Status: Revoked</b>\n\nYour access has been terminated by the Administrator."

    expires_ts = user.get("expires_ts")
    if expires_ts and time.time() > expires_ts:
        return (
            f"⏳ <b>Account Status: Expired</b>\n\n"
            f"Your subscription expired on <code>{user.get('expires_at')}</code>.\n"
            f"To renew, redeem a new key with <code>/redeem &lt;NEW_KEY&gt;</code>."
        )

    return (
        f"✅ <b>VIP Account Active</b>\n\n"
        f"• <b>Plan:</b> {user.get('plan_label')}\n"
        f"• <b>Expires on:</b> <code>{user.get('expires_at')}</code>\n"
        f"• <b>Key Code:</b> <code>{user.get('key_used')}</code>\n\n"
        f"You have full access to all Forex, Crypto & Memecoin signals!"
    )


def get_all_active_chat_ids() -> list[int]:
    """
    Returns list of all active chat IDs for broadcasting.
    """
    _load_store()
    ids = set()
    ids.add(ADMIN_CHAT_ID)

    for cid_str, u in _STORE["users"].items():
        if u.get("status") == "active":
            exp_ts = u.get("expires_ts")
            if not exp_ts or exp_ts > time.time():
                try:
                    ids.add(int(cid_str))
                except ValueError:
                    pass
    return list(ids)
