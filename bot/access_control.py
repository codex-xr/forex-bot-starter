import os
import json
import secrets
import time
import html
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "6686703329"))

# In-memory cache
_STORE: dict = {
    "keys": {},
    "users": {},
    "schedules": {},
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


_LOADED = False


def _load_store(force: bool = False) -> dict:
    global _STORE, _LOADED
    if _LOADED and not force:
        return _STORE
    try:
        path = _get_store_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                _STORE["keys"] = data.get("keys", {})
                _STORE["users"] = data.get("users", {})
                _STORE["schedules"] = data.get("schedules", {})
        _LOADED = True
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


def _clean_target(target: str) -> str:
    """Strips whitespace, @ signs, angle brackets <>, quotes, and punctuation."""
    if not target:
        return ""
    t = target.strip().strip("<>\"'").lstrip("@").strip("<>\"'").strip()
    return t


def is_admin(chat_id: int | str) -> bool:
    try:
        clean_id = _clean_target(str(chat_id))
        return int(clean_id) == ADMIN_CHAT_ID
    except (ValueError, TypeError):
        return False


def parse_duration(duration_str: str) -> tuple[int | None, str]:
    """
    Parses duration string like '1d', '7d', '30d', '90d', '365d', 'lifetime'.
    Returns (days, label).
    """
    s = _clean_target(duration_str).lower()
    if s in ("lifetime", "perm", "permanent", "unlimited"):
        return None, "Lifetime"
    if s.endswith("d") or s.endswith("days") or s.endswith("day"):
        num_str = "".join(filter(str.isdigit, s))
        days = int(num_str) if num_str else 30
        return days, f"{days} Day{'s' if days != 1 else ''}"
    if s.endswith("m") or s.endswith("months") or s.endswith("month"):
        num_str = "".join(filter(str.isdigit, s))
        months = int(num_str) if num_str else 1
        days = months * 30
        return days, f"{months} Month{'s' if months > 1 else ''}"
    if s.isdigit():
        days = int(s)
        return days, f"{days} Day{'s' if days != 1 else ''}"
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
    chat_id_str = _clean_target(str(chat_id))
    key_clean = _clean_target(key_code).upper()

    existing = _STORE["users"].get(chat_id_str)
    if existing and existing.get("status") == "revoked":
        return False, "🚫 <b>Account Revoked:</b> Your access has been terminated by the Administrator and cannot redeem keys."

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
        if existing and existing.get("status") == "active" and existing.get("expires_ts"):
            current_exp = datetime.fromtimestamp(existing["expires_ts"], tz=timezone.utc)
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
        "username": user_info.get("username", existing.get("username", "Unknown") if existing else "Unknown"),
        "first_name": user_info.get("first_name", existing.get("first_name", "User") if existing else "User"),
        "plan_label": key_data["label"],
        "activated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "expires_at": expires_at_str,
        "expires_ts": expires_ts,
        "status": "active",
        "key_used": key_clean,
        "last_active": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    _save_store()

    return True, f"🎉 <b>Access Activated Successfully!</b>\n\n• <b>Plan:</b> {html.escape(key_data['label'])}\n• <b>Expires:</b> <code>{html.escape(expires_at_str)}</code>\n\nYou now have full access to all Forex, Crypto & Memecoin signals! Type /help to begin."


def is_user_authorized(chat_id: int | str, user_info: dict | None = None) -> tuple[bool, str]:
    """
    Checks if a user is authorized to use the bot and automatically
    logs ALL visitors into the user database for tracking.
    """
    clean_id = _clean_target(str(chat_id))
    if is_admin(clean_id):
        return True, "Admin"

    _load_store()
    user = _STORE["users"].get(clean_id)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Auto-register new visitor
    if not user:
        _STORE["users"][clean_id] = {
            "chat_id": clean_id,
            "username": user_info.get("username", "Unknown") if user_info else "Unknown",
            "first_name": user_info.get("first_name", "Visitor") if user_info else "Visitor",
            "plan_label": "No Key (Pending)",
            "activated_at": now_str,
            "expires_at": "N/A",
            "expires_ts": None,
            "status": "pending",
            "key_used": "None",
            "last_active": now_str,
        }
        _save_store()
        return False, "unregistered"

    # Update activity for existing user
    if user_info:
        user["last_active"] = now_str
        if user_info.get("username"):
            user["username"] = user_info["username"]
        if user_info.get("first_name"):
            user["first_name"] = user_info["first_name"]
        _save_store()

    if user.get("status") == "revoked":
        return False, "revoked"

    if user.get("status") == "pending":
        return False, "unregistered"

    expires_ts = user.get("expires_ts")
    if expires_ts is not None:
        if time.time() > expires_ts:
            user["status"] = "expired"
            _save_store()
            return False, "expired"

    return True, "active"


def revoke_user(target: str) -> tuple[bool, str]:
    """
    Revokes / terminates access for ANY user by Chat ID or @username.
    """
    _load_store()
    target_clean = _clean_target(target).lower()

    if not target_clean:
        return False, "Please specify a valid user ID or username to revoke."

    found_id = None
    for cid, u in _STORE["users"].items():
        if cid.lower() == target_clean or (u.get("username") and u.get("username").lower() == target_clean):
            found_id = cid
            break

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not found_id:
        found_id = target_clean
        _STORE["users"][found_id] = {
            "chat_id": found_id,
            "username": target_clean if not target_clean.isdigit() else "Unknown",
            "first_name": "Revoked User",
            "plan_label": "Revoked",
            "activated_at": now_str,
            "expires_at": "Revoked",
            "expires_ts": None,
            "status": "revoked",
            "key_used": "Banned",
            "last_active": now_str,
            "revoked_at": now_str,
        }
        _save_store()
        return True, f"🚫 Access for <code>{html.escape(found_id)}</code> has been <b>REVOKED & BANNED</b>."

    user = _STORE["users"][found_id]
    user["status"] = "revoked"
    user["revoked_at"] = now_str
    _save_store()

    raw_name = f"@{user.get('username')}" if user.get("username") and user.get("username") != "Unknown" else user.get("first_name", found_id)
    safe_name = html.escape(str(raw_name))
    safe_id = html.escape(str(found_id))
    return True, f"🚫 Access for <b>{safe_name}</b> (ID: <code>{safe_id}</code>) has been <b>REVOKED & BANNED</b>."


def unban_user(target: str) -> tuple[bool, str]:
    """
    Unbans / restores access for a previously revoked user by Chat ID or @username.
    """
    _load_store()
    target_clean = _clean_target(target).lower()

    if not target_clean:
        return False, "Please specify a valid user ID or username to unban."

    found_id = None
    for cid, u in _STORE["users"].items():
        if cid.lower() == target_clean or (u.get("username") and u.get("username").lower() == target_clean):
            found_id = cid
            break

    if not found_id:
        return False, f"User '{html.escape(target)}' not found in the database."

    user = _STORE["users"][found_id]
    if user.get("status") != "revoked":
        return False, f"User is not revoked (current status: {html.escape(str(user.get('status')))})."

    expires_ts = user.get("expires_ts")
    if expires_ts and expires_ts > time.time():
        user["status"] = "active"
        status_msg = f"Active access restored (Expires on {html.escape(str(user.get('expires_at')))})"
    else:
        user["status"] = "pending"
        status_msg = "Unbanned (User can now redeem a key or receive an admin grant)"

    _save_store()
    raw_name = f"@{user.get('username')}" if user.get("username") and user.get("username") != "Unknown" else user.get("first_name", found_id)
    safe_name = html.escape(str(raw_name))
    safe_id = html.escape(str(found_id))
    return True, f"✅ Access for <b>{safe_name}</b> (ID: <code>{safe_id}</code>) has been <b>RESTORED</b>.\n• {status_msg}"


def grant_user(target: str, duration_str: str = "30d", admin_note: str = "Admin Grant") -> tuple[bool, str]:
    """
    Directly grants or extends access for a user without needing a key.
    """
    _load_store()
    target_clean = _clean_target(target)

    if not target_clean:
        return False, "Please specify a valid user ID or username."

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
    safe_id = html.escape(str(found_id))
    safe_label = html.escape(str(label))
    safe_exp = html.escape(str(expires_at_str))
    return True, f"✅ Access granted to ID <code>{safe_id}</code> ({safe_label}, expires: <code>{safe_exp}</code>)."


def list_users_report() -> str:
    """
    Formats clean Telegram report of ALL users (Active VIPs, Pending Visitors, Expired, Revoked).
    """
    _load_store()
    users = _STORE["users"]
    if not users:
        return "👥 <b>All Bot Users:</b>\n\n<i>No accounts have messaged the bot yet.</i>"

    active_users = []
    pending_users = []
    expired_users = []
    revoked_users = []

    for cid, u in users.items():
        st = u.get("status", "pending")
        if st == "active":
            if u.get("expires_ts") and u.get("expires_ts") <= time.time():
                expired_users.append((cid, u))
            else:
                active_users.append((cid, u))
        elif st == "revoked":
            revoked_users.append((cid, u))
        elif st == "expired":
            expired_users.append((cid, u))
        else:
            pending_users.append((cid, u))

    lines = [
        "👥 <b>Full User Dashboard & Access Tracker</b>",
        f"• <b>Total Tracked Accounts:</b> <code>{len(users)}</code>",
        f"• 🟢 <b>VIP Active:</b> <code>{len(active_users)}</code>",
        f"• 🔒 <b>Pending Visitors:</b> <code>{len(pending_users)}</code>",
        f"• ⏳ <b>Expired:</b> <code>{len(expired_users)}</code>",
        f"• 🚫 <b>Revoked / Banned:</b> <code>{len(revoked_users)}</code>",
        "",
    ]

    if active_users:
        lines.append("🟢 <b>Active VIP Subscribers:</b>")
        for cid, u in active_users:
            raw_name = f"@{u.get('username')}" if u.get("username") and u.get("username") != "Unknown" else u.get("first_name", "User")
            safe_name = html.escape(str(raw_name))
            safe_id = html.escape(str(cid))
            safe_plan = html.escape(str(u.get('plan_label', 'VIP')))
            safe_exp = html.escape(str(u.get('expires_at', 'N/A')))
            safe_last = html.escape(str(u.get('last_active', 'N/A')))
            lines.append(f"• <b>{safe_name}</b> (<code>{safe_id}</code>) — {safe_plan}")
            lines.append(f"  Expires: <code>{safe_exp}</code> | Seen: {safe_last}")
        lines.append("")

    if pending_users:
        lines.append("🔒 <b>Visitors (No Key / Pending):</b>")
        for cid, u in pending_users[-10:]:
            raw_name = f"@{u.get('username')}" if u.get("username") and u.get("username") != "Unknown" else u.get("first_name", "User")
            safe_name = html.escape(str(raw_name))
            safe_id = html.escape(str(cid))
            safe_last = html.escape(str(u.get('last_active', 'N/A')))
            lines.append(f"• <b>{safe_name}</b> (ID: <code>{safe_id}</code>) — Last seen: {safe_last}")
        lines.append("")

    if expired_users:
        lines.append("⏳ <b>Expired Accounts:</b>")
        for cid, u in expired_users[-5:]:
            raw_name = f"@{u.get('username')}" if u.get("username") and u.get("username") != "Unknown" else u.get("first_name", "User")
            safe_name = html.escape(str(raw_name))
            safe_id = html.escape(str(cid))
            safe_exp = html.escape(str(u.get('expires_at', 'N/A')))
            lines.append(f"• <b>{safe_name}</b> (<code>{safe_id}</code>) — Expired: {safe_exp}")
        lines.append("")

    if revoked_users:
        lines.append("🚫 <b>Revoked / Banned Users:</b>")
        for cid, u in revoked_users[-5:]:
            raw_name = f"@{u.get('username')}" if u.get("username") and u.get("username") != "Unknown" else u.get("first_name", "User")
            safe_name = html.escape(str(raw_name))
            safe_id = html.escape(str(cid))
            lines.append(f"• <b>{safe_name}</b> (<code>{safe_id}</code>) — Revoked")
        lines.append("")

    lines.append("<b>Admin Quick Actions:</b>")
    lines.append("• Terminate user: <code>/revoke 123456789</code>")
    lines.append("• Grant access: <code>/grant 123456789 30d</code>")

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
            note_str = f" ({html.escape(k['note'])})" if k.get("note") else ""
            lines.append(f"• <code>{html.escape(k['key'])}</code> — <b>{html.escape(k['label'])}</b>{note_str}")
        lines.append("")

    if redeemed:
        lines.append("<b>Recently Redeemed:</b>")
        for k in redeemed[-5:]:
            lines.append(f"• <code>{html.escape(k['key'])}</code> — Used by <code>{html.escape(str(k.get('used_by')))}</code> on {html.escape(str(k.get('used_at')))}")

    return "\n".join(lines).strip()


def get_user_plan_report(chat_id: int | str) -> str:
    """
    Formats plan status for a user viewing /myplan.
    """
    clean_id = _clean_target(str(chat_id))
    if is_admin(clean_id):
        return (
            "👑 <b>Account Status: Master Administrator</b>\n\n"
            "• <b>Privilege:</b> Permanent Full Access\n"
            "• <b>Admin Controls:</b> <code>/genkey</code>, <code>/users</code>, <code>/revoke</code>, <code>/unban</code>, <code>/grant</code>, <code>/keys</code>, <code>/broadcast</code>, <code>/schedule</code>, <code>/schedules</code>"
        )

    _load_store()
    user = _STORE["users"].get(clean_id)

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
            f"Your subscription expired on <code>{html.escape(str(user.get('expires_at')))}</code>.\n"
            f"To renew, redeem a new key with <code>/redeem &lt;NEW_KEY&gt;</code>."
        )

    if status == "pending":
        return (
            "🔒 <b>No Active Subscription</b>\n\n"
            "You have not redeemed a VIP key yet.\n"
            "Activate your account with: <code>/redeem &lt;YOUR_KEY&gt;</code>"
        )

    return (
        f"✅ <b>VIP Account Active</b>\n\n"
        f"• <b>Plan:</b> {html.escape(str(user.get('plan_label')))}\n"
        f"• <b>Expires on:</b> <code>{html.escape(str(user.get('expires_at')))}</code>\n"
        f"• <b>Key Code:</b> <code>{html.escape(str(user.get('key_used')))}</code>\n\n"
        f"You have full access to all Forex, Crypto & Memecoin signals!"
    )


def get_broadcast_recipients(only_vip: bool = False) -> list[int]:
    """
    Returns list of chat IDs for broadcasting announcements.
    If only_vip is False, broadcasts to ALL tracked users (VIPs, Visitors, Expired) except Revoked.
    """
    _load_store()
    ids = set()
    ids.add(ADMIN_CHAT_ID)

    for cid_str, u in _STORE["users"].items():
        st = u.get("status", "pending")
        if st == "revoked":
            continue

        if only_vip:
            if st == "active":
                exp_ts = u.get("expires_ts")
                if not exp_ts or exp_ts > time.time():
                    try:
                        ids.add(int(_clean_target(cid_str)))
                    except ValueError:
                        pass
        else:
            try:
                ids.add(int(_clean_target(cid_str)))
            except ValueError:
                pass

    return list(ids)


def get_all_active_chat_ids() -> list[int]:
    return get_broadcast_recipients(only_vip=False)


# ===========================================================================
# ⏰ SCHEDULED & RECURRING BROADCASTS ENGINE
# ===========================================================================

def parse_schedule_timing(time_expr: str) -> tuple[float | None, int | None, str, bool]:
    """
    Parses schedule timing expressions:
    - 'in 30m', '2h', '1d', 'in 45s' -> one-time delay
    - 'every 12h', 'every 24h', 'every 1d' -> recurring interval
    - 'daily 08:30', 'daily 08:30 utc', '08:30 utc' -> recurring daily at specific UTC time
    Returns (next_run_ts, interval_seconds, timing_label, is_recurring).
    """
    now = datetime.now(timezone.utc)
    s = _clean_target(time_expr).lower().replace("utc", "").strip()

    # Case 1: Daily at specific time (e.g. 'daily 08:30' or '08:30')
    daily_match = re.search(r"(?:daily\s+)?(\d{1,2}):(\d{2})", s)
    if daily_match and ("every" not in s or "daily" in s):
        hour = int(daily_match.group(1))
        minute = int(daily_match.group(2))
        target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_today <= now:
            target_time = target_today + timedelta(days=1)
        else:
            target_time = target_today

        interval = 86400  # 24 hours
        label = f"Daily at {hour:02d}:{minute:02d} UTC"
        return target_time.timestamp(), interval, label, True

    # Case 2: Recurring interval ('every 12h', 'every 1d', 'every 30m')
    if s.startswith("every"):
        rest = s.replace("every", "").strip()
        num_match = re.search(r"(\d+)\s*([a-zA-Z]+)", rest)
        if num_match:
            val = int(num_match.group(1))
            unit = num_match.group(2).lower()
            if unit.startswith("m"):
                sec = val * 60
                unit_label = "Minute" if val == 1 else "Minutes"
            elif unit.startswith("h"):
                sec = val * 3600
                unit_label = "Hour" if val == 1 else "Hours"
            elif unit.startswith("d"):
                sec = val * 86400
                unit_label = "Day" if val == 1 else "Days"
            elif unit.startswith("s"):
                sec = val
                unit_label = "Second" if val == 1 else "Seconds"
            else:
                sec = val * 3600
                unit_label = "Hours"

            next_run = (now + timedelta(seconds=sec)).timestamp()
            label = f"Recurring every {val} {unit_label}"
            return next_run, sec, label, True

    # Case 3: One-time delay ('in 2h', '30m', '1d', 'in 45s')
    clean_once = s.replace("in", "").strip()
    num_match = re.search(r"(\d+)\s*([a-zA-Z]+)", clean_once)
    if num_match:
        val = int(num_match.group(1))
        unit = num_match.group(2).lower()
        if unit.startswith("m"):
            sec = val * 60
            unit_label = "Minute" if val == 1 else "Minutes"
        elif unit.startswith("h"):
            sec = val * 3600
            unit_label = "Hour" if val == 1 else "Hours"
        elif unit.startswith("d"):
            sec = val * 86400
            unit_label = "Day" if val == 1 else "Days"
        elif unit.startswith("s"):
            sec = val
            unit_label = "Second" if val == 1 else "Seconds"
        else:
            sec = val * 3600
            unit_label = "Hours"

        next_run = (now + timedelta(seconds=sec)).timestamp()
        label = f"Once in {val} {unit_label}"
        return next_run, None, label, False

    return None, None, "Invalid Timing", False


def create_scheduled_broadcast(time_expr: str, message_text: str, target: str = "all") -> tuple[bool, str]:
    """
    Creates and stores a scheduled or recurring broadcast.
    """
    _load_store()
    next_run_ts, interval, label, is_recurring = parse_schedule_timing(time_expr)

    if not next_run_ts:
        return False, (
            "⚠️ <b>Invalid Timing Format.</b>\n\n"
            "<b>Valid Examples:</b>\n"
            "• One-time: <code>/schedule in 2h Message</code>\n"
            "• Recurring: <code>/schedule every 24h Message</code>\n"
            "• Daily time: <code>/schedule daily 08:30 Message</code>"
        )

    raw_msg = _clean_target(message_text)
    if not raw_msg:
        return False, "⚠️ Please provide a message text for the scheduled broadcast."

    sched_id = f"SB-{secrets.token_hex(2).upper()}"
    next_date_str = datetime.fromtimestamp(next_run_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    _STORE["schedules"][sched_id] = {
        "id": sched_id,
        "message": raw_msg,
        "target": target.lower(),
        "is_recurring": is_recurring,
        "interval_seconds": interval,
        "timing_label": label,
        "next_run_ts": next_run_ts,
        "next_run_str": next_date_str,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "status": "active",
        "executions_count": 0,
        "last_executed_at": None,
    }
    _save_store()

    safe_id = html.escape(sched_id)
    safe_label = html.escape(label)
    safe_date = html.escape(next_date_str)
    safe_preview = html.escape(raw_msg[:100] + ("..." if len(raw_msg) > 100 else ""))

    rec_type = "🔁 <b>Recurring Schedule Created</b>" if is_recurring else "⏰ <b>One-Time Schedule Created</b>"

    return True, (
        f"{rec_type}\n\n"
        f"• <b>ID:</b> <code>{safe_id}</code>\n"
        f"• <b>Timing:</b> {safe_label}\n"
        f"• <b>First Run:</b> <code>{safe_date}</code>\n"
        f"• <b>Audience:</b> <code>{target.upper()}</code>\n"
        f"• <b>Message:</b> <i>'{safe_preview}'</i>\n\n"
        f"<i>To cancel anytime: <code>/cancelschedule {safe_id}</code></i>"
    )


def cancel_schedule(sched_id: str) -> tuple[bool, str]:
    """
    Cancels and deletes a scheduled broadcast.
    """
    _load_store()
    clean_id = _clean_target(sched_id).upper()

    if clean_id not in _STORE["schedules"]:
        return False, f"Schedule ID <code>{html.escape(clean_id)}</code> not found."

    sched = _STORE["schedules"][clean_id]
    del _STORE["schedules"][clean_id]
    _save_store()

    return True, f"🗑️ Scheduled broadcast <code>{html.escape(clean_id)}</code> (<i>{html.escape(sched.get('timing_label', ''))}</i>) has been <b>CANCELLED</b>."


def list_schedules_report() -> str:
    """
    Lists all active and recent scheduled broadcasts.
    """
    _load_store()
    schedules = _STORE.get("schedules", {})
    if not schedules:
        return (
            "⏰ <b>Scheduled Broadcasts Dashboard</b>\n\n"
            "<i>No broadcasts currently scheduled.</i>\n\n"
            "<b>To schedule an announcement:</b>\n"
            "• <code>/schedule in 2h &lt;message&gt;</code>\n"
            "• <code>/schedule every 24h &lt;message&gt;</code>\n"
            "• <code>/schedule daily 08:30 &lt;message&gt;</code>"
        )

    lines = [
        "⏰ <b>Scheduled Broadcasts Dashboard</b>",
        f"• <b>Total Active Schedules:</b> <code>{len(schedules)}</code>",
        "",
    ]

    for sid, s in schedules.items():
        emoji = "🔁" if s.get("is_recurring") else "⏰"
        safe_id = html.escape(sid)
        safe_label = html.escape(str(s.get("timing_label", "Scheduled")))
        safe_next = html.escape(str(s.get("next_run_str", "N/A")))
        safe_msg = html.escape(str(s.get("message", "")[:80]) + ("..." if len(str(s.get("message", ""))) > 80 else ""))
        exec_count = s.get("executions_count", 0)

        lines.append(f"{emoji} <b>{safe_id}</b> — {safe_label}")
        lines.append(f"   • Next run: <code>{safe_next}</code> | Sent: {exec_count} times")
        lines.append(f"   • Message: <i>'{safe_msg}'</i>")
        lines.append(f"   • Cancel: <code>/cancelschedule {safe_id}</code>")
        lines.append("")

    return "\n".join(lines).strip()


def process_due_broadcasts() -> list[dict]:
    """
    Checks and executes any due scheduled broadcasts.
    Dispatches Telegram messages and advances recurring schedules.
    """
    from bot.telegram import send_telegram_message

    _load_store()
    schedules = _STORE.get("schedules", {})
    if not schedules:
        return []

    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)
    executed = []
    to_delete = []

    for sid, s in list(schedules.items()):
        if s.get("status") != "active":
            continue

        next_ts = s.get("next_run_ts", float("inf"))
        if now_ts >= next_ts:
            # Broadcast is due!
            raw_msg = s.get("message", "")
            safe_msg = html.escape(raw_msg)
            broadcast_text = f"📢 <b>ADMIN ANNOUNCEMENT</b>\n\n{safe_msg}"

            target = s.get("target", "all")
            recipients = get_broadcast_recipients(only_vip=(target == "vip"))

            sent_count = 0
            for cid in recipients:
                try:
                    send_telegram_message(broadcast_text, chat_id=cid)
                    sent_count += 1
                    time.sleep(0.04)
                except Exception as exc:
                    print(f"[ScheduledBroadcast] Error sending to {cid}: {exc}")

            s["executions_count"] = s.get("executions_count", 0) + 1
            s["last_executed_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")

            executed.append({
                "id": sid,
                "sent_count": sent_count,
                "label": s.get("timing_label"),
            })

            # Handle recurrence
            if s.get("is_recurring") and s.get("interval_seconds"):
                s["next_run_ts"] += s["interval_seconds"]
                s["next_run_str"] = datetime.fromtimestamp(s["next_run_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            else:
                s["status"] = "completed"
                to_delete.append(sid)

    # Clean up completed one-time schedules
    for sid in to_delete:
        del schedules[sid]

    if executed:
        _save_store()

    return executed
