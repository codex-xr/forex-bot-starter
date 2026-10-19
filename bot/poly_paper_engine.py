from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import time
import requests
from dotenv import load_dotenv

from bot.polymarket_engine import PolymarketSignal, get_live_share_price
from bot.telegram import send_telegram_message

load_dotenv()

DEFAULT_POLY_USER_ID = str(os.getenv("TELEGRAM_CHAT_ID", "6686703329")).split(",")[0].strip()


def _clean_uid(user_id: int | str | None) -> str:
    if user_id is None:
        return DEFAULT_POLY_USER_ID
    s = str(user_id).strip().strip("<>\"'")
    return s if s else DEFAULT_POLY_USER_ID


@dataclass
class PolyPosition:
    pos_id: str
    market_id: str
    question: str
    category: str
    side: str            # "YES" or "NO"
    confidence: int
    entry_price: float   # Share price ($0.01 - $0.99)
    tp1: float
    tp2: float
    sl: float
    bet_size_usd: float  # e.g. $10.00
    shares: float        # bet_size_usd / entry_price
    entry_time: str
    entry_ts: float
    user_id: str = DEFAULT_POLY_USER_ID


@dataclass
class PolyClosedTrade:
    pos_id: str
    market_id: str
    question: str
    side: str
    entry_price: float
    exit_price: float
    bet_size_usd: float
    shares: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: str     # "TP1_HIT", "SL_HIT", "RESOLVED_WIN", "RESOLVED_LOSS", "MANUAL_CLOSE"
    entry_time: str
    exit_time: str
    exit_date: str


@dataclass
class UserPolyState:
    virtual_balance: float = 100.0
    initial_balance: float = 100.0
    bet_size_usd: float = 10.0
    positions: dict[str, PolyPosition] = field(default_factory=dict)
    closed_trades: list[PolyClosedTrade] = field(default_factory=list)


class MultiUserPolyStore:
    def __init__(self) -> None:
        self.users: dict[str, UserPolyState] = {}

    def get_user_state(self, user_id: int | str | None = None) -> UserPolyState:
        uid = _clean_uid(user_id)
        if uid not in self.users:
            self.users[uid] = UserPolyState()
        return self.users[uid]


_POLY_STORE: MultiUserPolyStore | None = None
_LAST_POLY_SYNC_TS: float = 0.0


def _get_poly_store_path() -> Path:
    try:
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test_poly"
        test_file.touch()
        test_file.unlink()
        return data_dir / "poly_paper_store.json"
    except (OSError, PermissionError):
        tmp_dir = Path("/tmp/forex_bot_data")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir / "poly_paper_store.json"


def _load_poly_from_cloud() -> dict | None:
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(2):
        try:
            res = requests.get(f"{url}/get/poly_paper_multi_store", headers=headers, timeout=5)
            if res.status_code == 200:
                val = res.json().get("result")
                if val:
                    return json.loads(val) if isinstance(val, str) else val
            return None
        except Exception as exc:
            if attempt == 1:
                print(f"[PolyPaperEngine] Cloud load error: {exc}")
            time.sleep(0.3)
    return None


def _save_poly_to_cloud(data: dict) -> bool:
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return False
    headers = {"Authorization": f"Bearer {token}"}
    payload_str = json.dumps(data)
    for attempt in range(2):
        try:
            res = requests.post(
                url,
                headers=headers,
                json=["SET", "poly_paper_multi_store", payload_str],
                timeout=5,
            )
            if res.status_code == 200:
                return True
        except Exception as exc:
            if attempt == 1:
                print(f"[PolyPaperEngine] Cloud save error: {exc}")
            time.sleep(0.3)
    return False


def _deserialize_user_poly(u_raw: dict) -> UserPolyState:
    ustate = UserPolyState(
        virtual_balance=float(u_raw.get("virtual_balance", 100.0)),
        initial_balance=float(u_raw.get("initial_balance", 100.0)),
        bet_size_usd=float(u_raw.get("bet_size_usd", 10.0)),
    )
    for k, v in u_raw.get("positions", {}).items():
        ustate.positions[k] = PolyPosition(**v)
    for t in u_raw.get("closed_trades", []):
        ustate.closed_trades.append(PolyClosedTrade(**t))
    return ustate


def _serialize_poly_store(store: MultiUserPolyStore) -> dict:
    users_dict = {}
    for uid, ustate in store.users.items():
        users_dict[uid] = {
            "virtual_balance": ustate.virtual_balance,
            "initial_balance": ustate.initial_balance,
            "bet_size_usd": ustate.bet_size_usd,
            "positions": {k: asdict(v) for k, v in ustate.positions.items()},
            "closed_trades": [asdict(t) for t in ustate.closed_trades[-100:]],
        }
    return {"users": users_dict}


def _load_poly_store(force: bool = False) -> MultiUserPolyStore:
    global _POLY_STORE, _LAST_POLY_SYNC_TS
    now = time.time()
    if _POLY_STORE is not None and not force and (now - _LAST_POLY_SYNC_TS < 2.0):
        return _POLY_STORE

    store = MultiUserPolyStore()
    raw = None
    if not os.getenv("PYTEST_CURRENT_TEST"):
        raw = _load_poly_from_cloud()

    if not raw:
        path = _get_poly_store_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as exc:
                print(f"[PolyPaperEngine] Local load error: {exc}")

    if raw and isinstance(raw, dict):
        for uid, u_data in raw.get("users", {}).items():
            if isinstance(u_data, dict):
                try:
                    store.users[str(uid)] = _deserialize_user_poly(u_data)
                except Exception as e:
                    print(f"[PolyPaperEngine] Deserialize error for {uid}: {e}")

    _POLY_STORE = store
    _LAST_POLY_SYNC_TS = now
    return store


def _save_poly_store(store: MultiUserPolyStore) -> None:
    global _POLY_STORE, _LAST_POLY_SYNC_TS
    _POLY_STORE = store
    _LAST_POLY_SYNC_TS = time.time()
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    data = _serialize_poly_store(store)
    try:
        path = _get_poly_store_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        print(f"[PolyPaperEngine] Local save error: {exc}")

    _save_poly_to_cloud(data)


def get_poly_settings(user_id: int | str | None = None) -> dict:
    store = _load_poly_store()
    ustate = store.get_user_state(user_id)
    return {
        "virtual_balance": ustate.virtual_balance,
        "initial_balance": ustate.initial_balance,
        "bet_size_usd": ustate.bet_size_usd,
        "open_positions_count": len(ustate.positions),
    }


def set_poly_bet_size(amount: float, user_id: int | str | None = None) -> tuple[bool, str]:
    if amount <= 0:
        return False, "❌ Bet amount must be greater than $0."
    store = _load_poly_store(force=True)
    ustate = store.get_user_state(user_id)
    ustate.bet_size_usd = round(float(amount), 2)
    _save_poly_store(store)
    return True, (
        f"✅ <b>Polymarket Bet Amount Updated!</b>\n\n"
        f"• <b>New Bet Per Pick:</b> <code>${ustate.bet_size_usd:,.2f}</code>\n"
        f"• <b>Prediction Wallet Balance:</b> <code>${ustate.virtual_balance:,.2f}</code>\n\n"
        f"<i>Every new prediction you enter via <code>/poly</code> or <code>/penter</code> will now use <code>${ustate.bet_size_usd:,.2f}</code>.</i>"
    )


def reset_poly_wallet(starting_balance: float = 100.0, user_id: int | str | None = None) -> tuple[bool, str]:
    if starting_balance <= 0:
        return False, "❌ Starting balance must be greater than $0."
    store = _load_poly_store(force=True)
    ustate = store.get_user_state(user_id)
    ustate.virtual_balance = round(float(starting_balance), 2)
    ustate.initial_balance = round(float(starting_balance), 2)
    ustate.positions.clear()
    ustate.closed_trades.clear()
    _save_poly_store(store)
    return True, (
        f"🔄 <b>Polymarket Paper Wallet Reset!</b>\n\n"
        f"• <b>Starting Balance:</b> <code>${ustate.virtual_balance:,.2f}</code>\n"
        f"• <b>Default Bet Size:</b> <code>${ustate.bet_size_usd:,.2f}</code>\n"
        f"• <b>Open Bets & History:</b> Cleared"
    )


def open_poly_trade(
    signal: PolymarketSignal,
    user_id: int | str | None = None,
) -> tuple[bool, str, PolyPosition | None]:
    uid = _clean_uid(user_id)
    store = _load_poly_store(force=True)
    ustate = store.get_user_state(uid)

    pos_id = f"{signal.market_id}_{signal.side}"
    if pos_id in ustate.positions:
        existing = ustate.positions[pos_id]
        return False, (
            f"⚠️ You already have an active <b>{existing.side}</b> prediction on:\n"
            f"<i>{html.escape(existing.question)}</i>\n\n"
            f"Use <code>/pstatus</code> to view or cash it out."
        ), existing

    bet_amt = ustate.bet_size_usd
    if ustate.virtual_balance < bet_amt:
        return False, (
            f"❌ <b>Insufficient Prediction Wallet Balance!</b>\n\n"
            f"• <b>Available Balance:</b> <code>${ustate.virtual_balance:,.2f}</code>\n"
            f"• <b>Required Bet Size:</b> <code>${bet_amt:,.2f}</code>\n\n"
            f"Lower your bet size with <code>/polybet &lt;amount&gt;</code> or reset your wallet with <code>/pwallet reset 100</code>."
        ), None

    now = datetime.now(timezone.utc)
    shares = round(bet_amt / signal.entry_price, 4) if signal.entry_price > 0 else 0.0
    tp1_profit = shares * (signal.tp1 - signal.entry_price)
    sl_loss = shares * (signal.entry_price - signal.sl)

    pos = PolyPosition(
        pos_id=pos_id,
        market_id=signal.market_id,
        question=signal.question,
        category=signal.category,
        side=signal.side,
        confidence=signal.confidence,
        entry_price=signal.entry_price,
        tp1=signal.tp1,
        tp2=signal.tp2,
        sl=signal.sl,
        bet_size_usd=bet_amt,
        shares=shares,
        entry_time=now.strftime("%Y-%m-%d %H:%M UTC"),
        entry_ts=now.timestamp(),
        user_id=uid,
    )

    ustate.positions[pos_id] = pos
    _save_poly_store(store)

    side_emoji = "🟢" if signal.side == "YES" else "🔴"
    msg = (
        f"🎯 <b>POLYMARKET PAPER BET OPENED!</b>\n\n"
        f"📌 <b>{html.escape(signal.question)}</b>\n"
        f"• <b>Pick:</b> {side_emoji} <b>BUY {signal.side}</b> (<code>{signal.confidence}%</code> Confidence)\n"
        f"• <b>Bet Amount:</b> <code>${bet_amt:,.2f}</code> (<code>{shares:.2f} shares</code>)\n"
        f"• <b>Entry Odds:</b> <code>{signal.entry_price * 100:.1f}¢</code> (<code>${signal.entry_price:.3f}</code>)\n"
        f"• <b>TP 1 (Auto Cash-Out):</b> <code>{signal.tp1 * 100:.1f}¢</code> (<code>+${tp1_profit:.2f}</code> / <b>+{signal.tp1_roi_pct:.1f}%</b>)\n"
        f"• <b>Stop Loss:</b> <code>{signal.sl * 100:.1f}¢</code> (<code>-${sl_loss:.2f}</code> / <b>{signal.sl_roi_pct:.1f}%</b>)\n\n"
        f"🤖 <i>24/7 Watcher is now monitoring this prediction and will automatically cash out at TP1 or SL even while you are offline!</i>"
    )
    return True, msg, pos


def close_poly_trade(
    target: str,
    exit_price: float | None = None,
    reason: str = "MANUAL_CLOSE",
    user_id: int | str | None = None,
) -> tuple[bool, str, PolyClosedTrade | None]:
    uid = _clean_uid(user_id)
    store = _load_poly_store(force=True)
    ustate = store.get_user_state(uid)

    if not ustate.positions:
        return False, "ℹ️ You have no open Polymarket predictions to close.", None

    clean_t = str(target).strip().lstrip("#")
    found_key = None

    # 1. Match by exact pos_id or market_id
    if clean_t in ustate.positions:
        found_key = clean_t
    else:
        pos_keys = list(ustate.positions.keys())
        # 2. Match by 1-based index (e.g. /pclose 1)
        if clean_t.isdigit():
            idx = int(clean_t) - 1
            if 0 <= idx < len(pos_keys):
                found_key = pos_keys[idx]
        # 3. Match by market_id prefix or question keyword
        if not found_key:
            for k, p in ustate.positions.items():
                if p.market_id == clean_t or clean_t.lower() in p.question.lower():
                    found_key = k
                    break

    if not found_key:
        return False, f"❌ Could not find open prediction matching '<code>{html.escape(clean_t)}</code>'. Use <code>/pstatus</code> to check your active bet numbers.", None

    pos = ustate.positions.pop(found_key)

    if exit_price is None or exit_price <= 0:
        live_p, _, _ = get_live_share_price(pos.market_id, pos.side)
        exit_price = live_p if (live_p is not None and live_p > 0) else pos.entry_price

    exit_price = round(float(exit_price), 4)
    pnl_usd = round(pos.shares * (exit_price - pos.entry_price), 2)
    pnl_pct = round(((exit_price - pos.entry_price) / pos.entry_price) * 100.0, 2) if pos.entry_price > 0 else 0.0

    ustate.virtual_balance = round(ustate.virtual_balance + pnl_usd, 2)
    now = datetime.now(timezone.utc)

    closed = PolyClosedTrade(
        pos_id=pos.pos_id,
        market_id=pos.market_id,
        question=pos.question,
        side=pos.side,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        bet_size_usd=pos.bet_size_usd,
        shares=pos.shares,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        exit_reason=reason,
        entry_time=pos.entry_time,
        exit_time=now.strftime("%Y-%m-%d %H:%M UTC"),
        exit_date=now.strftime("%Y-%m-%d"),
    )
    ustate.closed_trades.append(closed)
    _save_poly_store(store)

    is_win = pnl_usd >= 0
    header_Map = {
        "TP1_HIT": "✅ <b>POLYMARKET TP1 HIT — PROFIT LOCKED!</b> 💰",
        "SL_HIT": "🛑 <b>POLYMARKET STOP LOSS HIT — RISK CAPPED</b>",
        "RESOLVED_WIN": "🏆 <b>POLYMARKET EVENT RESOLVED — FULL $1.00 WIN!</b> 🎉",
        "RESOLVED_LOSS": "❌ <b>POLYMARKET EVENT RESOLVED — LOSS</b>",
        "MANUAL_CLOSE": "💼 <b>POLYMARKET PREDICTION CASHED OUT</b>",
    }
    header = header_Map.get(reason, "💼 <b>POLYMARKET PREDICTION CLOSED</b>")
    sign = "+" if pnl_usd >= 0 else ""
    emoji_pnl = "🟢" if is_win else "🔴"

    msg = (
        f"{header}\n\n"
        f"📌 <b>{html.escape(pos.question)}</b>\n"
        f"• <b>Position:</b> BUY {pos.side} (<code>${pos.bet_size_usd:,.2f}</code> bet)\n"
        f"• <b>Entry Odds:</b> <code>{pos.entry_price * 100:.1f}¢</code> → <b>Exit Odds:</b> <code>{exit_price * 100:.1f}¢</code>\n"
        f"• <b>Realized PnL:</b> {emoji_pnl} <b>{sign}${pnl_usd:,.2f} ({sign}{pnl_pct:.1f}%)</b>\n"
        f"• <b>New Prediction Wallet Balance:</b> <code>${ustate.virtual_balance:,.2f}</code>"
    )
    return True, msg, closed


def close_all_poly_trades(user_id: int | str | None = None) -> tuple[int, str]:
    uid = _clean_uid(user_id)
    store = _load_poly_store(force=True)
    ustate = store.get_user_state(uid)
    if not ustate.positions:
        return 0, "ℹ️ You have no open Polymarket predictions to close."

    keys = list(ustate.positions.keys())
    total_pnl = 0.0
    count = 0
    for k in keys:
        ok, _, closed = close_poly_trade(k, reason="MANUAL_CLOSE", user_id=uid)
        if ok and closed:
            total_pnl += closed.pnl_usd
            count += 1

    store = _load_poly_store()
    new_bal = store.get_user_state(uid).virtual_balance
    sign = "+" if total_pnl >= 0 else ""
    return count, (
        f"✅ <b>Cashed Out All {count} Polymarket Predictions!</b>\n\n"
        f"• <b>Net Realized PnL:</b> <code>{sign}${total_pnl:,.2f}</code>\n"
        f"• <b>Prediction Wallet Balance:</b> <code>${new_bal:,.2f}</code>"
    )


def check_open_poly_trades(user_id: int | str | None = None) -> list[str]:
    """
    24/7 Background Watcher for Polymarket Paper Predictions.
    Checks live share prices for all open predictions across users (or a specific user)
    and automatically cashes out when TP1, SL, or Event Resolution is reached!
    """
    store = _load_poly_store(force=True)
    alerts_sent: list[str] = []

    target_uids = [_clean_uid(user_id)] if user_id is not None else list(store.users.keys())

    for uid in target_uids:
        ustate = store.users.get(uid)
        if not ustate or not ustate.positions:
            continue

        for pos_id, pos in list(ustate.positions.items()):
            curr_price, is_resolved, winning_side = get_live_share_price(pos.market_id, pos.side)
            if curr_price is None:
                continue

            trigger_reason = None
            exit_p = curr_price

            if is_resolved and winning_side is not None:
                if winning_side == pos.side:
                    trigger_reason = "RESOLVED_WIN"
                    exit_p = 1.00
                else:
                    trigger_reason = "RESOLVED_LOSS"
                    exit_p = 0.00
            elif curr_price >= pos.tp1:
                trigger_reason = "TP1_HIT"
                exit_p = max(curr_price, pos.tp1)
            elif curr_price <= pos.sl:
                trigger_reason = "SL_HIT"
                exit_p = min(curr_price, pos.sl)

            if trigger_reason:
                ok, alert_msg, _ = close_poly_trade(
                    pos_id,
                    exit_price=exit_p,
                    reason=trigger_reason,
                    user_id=uid,
                )
                if ok:
                    alerts_sent.append(alert_msg)
                    try:
                        send_telegram_message(alert_msg, chat_id=uid)
                    except Exception as exc:
                        print(f"[PolyPaperEngine] Failed to send alert to {uid}: {exc}")

    return alerts_sent


def get_poly_status_report(user_id: int | str | None = None) -> tuple[str, dict | None]:
    """
    Formats /pstatus showing all open Polymarket predictions, live share prices,
    live dollar PnL, and 1-tap Cash Out buttons.
    """
    uid = _clean_uid(user_id)
    try:
        check_open_poly_trades(user_id=uid)
    except Exception as exc:
        print(f"[PolyPaperEngine] Auto-check error in /pstatus: {exc}")

    store = _load_poly_store(force=True)
    ustate = store.get_user_state(uid)

    if not ustate.positions:
        msg = (
            f"🔮 <b>Polymarket Active Predictions (/pstatus)</b>\n"
            f"• <b>Wallet Balance:</b> <code>${ustate.virtual_balance:,.2f}</code>\n"
            f"• <b>Bet Size Per Pick:</b> <code>${ustate.bet_size_usd:,.2f}</code>\n\n"
            f"<i>You currently have no open predictions.</i>\n"
            f"Send <code>/poly</code> to scan the Top 5 high-confidence picks!"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "🔮 Scan Top 5 Predictions (/poly)", "callback_data": "/poly"}],
                [{"text": "💼 Prediction Wallet (/pwallet)", "callback_data": "/pwallet"}],
                [{"text": "📱 Main Menu (/menu)", "callback_data": "/menu"}],
            ]
        }
        return msg, kb

    lines = [
        "🔮 <b>Active Polymarket Predictions (Watched 24/7)</b>",
        f"💼 <b>Wallet Balance:</b> <code>${ustate.virtual_balance:,.2f}</code> | 🎯 <b>Bet Size:</b> <code>${ustate.bet_size_usd:,.2f}</code>",
        "",
    ]

    buttons = []
    total_floating_pnl = 0.0

    for idx, (pos_id, pos) in enumerate(list(ustate.positions.items()), start=1):
        live_p, _, _ = get_live_share_price(pos.market_id, pos.side)
        curr_p = live_p if (live_p is not None and live_p > 0) else pos.entry_price

        # Second-pass safety check: never show an open trade past TP1 or SL without closing it!
        if curr_p >= pos.tp1:
            ok, alert_msg, _ = close_poly_trade(pos_id, exit_price=max(curr_p, pos.tp1), reason="TP1_HIT", user_id=uid)
            if ok:
                try:
                    send_telegram_message(alert_msg, chat_id=uid)
                except Exception:
                    pass
                continue
        elif curr_p <= pos.sl:
            ok, alert_msg, _ = close_poly_trade(pos_id, exit_price=min(curr_p, pos.sl), reason="SL_HIT", user_id=uid)
            if ok:
                try:
                    send_telegram_message(alert_msg, chat_id=uid)
                except Exception:
                    pass
                continue

        pnl_usd = pos.shares * (curr_p - pos.entry_price)
        pnl_pct = ((curr_p - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price > 0 else 0.0
        total_floating_pnl += pnl_usd

        sign = "+" if pnl_usd >= 0 else ""
        pnl_icon = "🟢" if pnl_usd >= 0 else "🔴"

        lines.append(
            f"<b>#{idx}. {html.escape(pos.question)}</b>\n"
            f"• Pick: <b>BUY {pos.side}</b> (<code>{pos.confidence}%</code>) | Bet: <code>${pos.bet_size_usd:,.2f}</code>\n"
            f"• Entry: <code>{pos.entry_price * 100:.1f}¢</code> → <b>Live: <code>{curr_p * 100:.1f}¢</code></b>\n"
            f"• Live PnL: {pnl_icon} <b>{sign}${pnl_usd:,.2f} ({sign}{pnl_pct:.1f}%)</b>\n"
            f"• Targets: TP1 <code>{pos.tp1 * 100:.1f}¢</code> | SL <code>{pos.sl * 100:.1f}¢</code>"
        )
        lines.append("")
        short_q = pos.question[:22] + "..." if len(pos.question) > 22 else pos.question
        buttons.append([
            {"text": f"💰 Cash Out #{idx}: {pos.side} ({sign}${pnl_usd:.2f})", "callback_data": f"poly_close:{pos_id}"}
        ])

    net_sign = "+" if total_floating_pnl >= 0 else ""
    lines.append(f"📊 <b>Total Floating PnL:</b> <code>{net_sign}${total_floating_pnl:,.2f}</code>")

    buttons.append([
        {"text": "🔄 Refresh Live Odds", "callback_data": "/pstatus"},
        {"text": "❌ Cash Out All", "callback_data": "/pclose all"},
    ])
    buttons.append([
        {"text": "🔮 Prediction Menu", "callback_data": "menu_poly"},
        {"text": "📱 Main Menu", "callback_data": "/menu"},
    ])

    return "\n".join(lines).strip(), {"inline_keyboard": buttons}


def get_poly_wallet_report(user_id: int | str | None = None) -> tuple[str, dict]:
    """
    Formats /pwallet showing the user's Polymarket paper balance, bet size,
    win rate, total profit/loss, and recent closed bets.
    """
    uid = _clean_uid(user_id)
    store = _load_poly_store(force=True)
    ustate = store.get_user_state(uid)

    closed = ustate.closed_trades
    wins = [t for t in closed if t.pnl_usd >= 0]
    losses = [t for t in closed if t.pnl_usd < 0]
    total_trades = len(closed)
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    realized_pnl = sum(t.pnl_usd for t in closed)
    net_growth_pct = ((ustate.virtual_balance - ustate.initial_balance) / ustate.initial_balance * 100.0) if ustate.initial_balance > 0 else 0.0

    sign = "+" if realized_pnl >= 0 else ""
    growth_sign = "+" if net_growth_pct >= 0 else ""

    lines = [
        "💼 <b>Polymarket Prediction Wallet (/pwallet)</b>",
        "",
        f"• <b>Current Balance:</b> <code>${ustate.virtual_balance:,.2f}</code> (<b>{growth_sign}{net_growth_pct:.1f}%</b>)",
        f"• <b>Starting Balance:</b> <code>${ustate.initial_balance:,.2f}</code>",
        f"• <b>Bet Amount Per Pick:</b> <code>${ustate.bet_size_usd:,.2f}</code>",
        f"• <b>Active Open Bets:</b> <code>{len(ustate.positions)}</code>",
        "",
        "📈 <b>Prediction Performance:</b>",
        f"• <b>Total Realized PnL:</b> <code>{sign}${realized_pnl:,.2f}</code>",
        f"• <b>Win / Loss Record:</b> <code>{len(wins)}W - {len(losses)}L</code> (<b>{win_rate:.1f}% Win Rate</b>)",
        "",
    ]

    if closed:
        lines.append("📜 <b>Recent Closed Predictions:</b>")
        for t in reversed(closed[-5:]):
            t_sign = "+" if t.pnl_usd >= 0 else ""
            icon = "✅" if t.pnl_usd >= 0 else "🛑"
            short_q = t.question[:38] + "..." if len(t.question) > 38 else t.question
            lines.append(
                f"• {icon} <b>{html.escape(short_q)}</b> ({t.side})\n"
                f"  <code>{t.entry_price * 100:.0f}¢ → {t.exit_price * 100:.0f}¢</code> | <b>{t_sign}${t.pnl_usd:.2f}</b> ({t.exit_reason})"
            )
        lines.append("")

    lines.append("<i>Change bet size anytime with <code>/polybet &lt;amount&gt;</code> or reset with <code>/pwallet reset 100</code>.</i>")

    kb = {
        "inline_keyboard": [
            [
                {"text": "💵 Bet $5", "callback_data": "/polybet 5"},
                {"text": "💵 Bet $10", "callback_data": "/polybet 10"},
                {"text": "💵 Bet $25", "callback_data": "/polybet 25"},
                {"text": "💵 Bet $50", "callback_data": "/polybet 50"},
            ],
            [
                {"text": "🔮 Scan Predictions (/poly)", "callback_data": "/poly"},
                {"text": "📊 Open Bets (/pstatus)", "callback_data": "/pstatus"},
            ],
            [
                {"text": "🔄 Reset Wallet ($100)", "callback_data": "/pwallet reset 100"},
                {"text": "📱 Main Menu (/menu)", "callback_data": "/menu"},
            ],
        ]
    }
    return "\n".join(lines).strip(), kb
