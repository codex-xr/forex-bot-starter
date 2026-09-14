import datetime
import html
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
import requests

from bot.market_data import fetch_live_candles
from bot.symbols import DISPLAY_NAMES, normalize_symbol

# Cloud Persistence (Upstash Redis)
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
REDIS_PAPER_KEY = "paper_trading_data"


def _get_local_store_path() -> Path:
    if os.name == "nt":
        base_dir = Path(os.getenv("TEMP", "C:/temp")) / "forex_bot_data"
    else:
        base_dir = Path("/tmp/forex_bot_data")
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "paper_store.json"


def _fmt_price(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1000:
        return f"{val:.2f}"
    if abs(val) >= 10:
        return f"{val:.3f}"
    if abs(val) >= 0.1:
        return f"{val:.5f}"
    if abs(val) >= 0.0001:
        return f"{val:.8f}"
    return f"{val:.10f}"


@dataclass
class PaperSettings:
    virtual_balance: float = 10000.0
    trade_size_usd: float = 1000.0
    leverage: float = 1.0


@dataclass
class PaperPosition:
    id: str
    symbol: str
    display_symbol: str
    action: str  # "BUY" / "SELL" (or "LONG" / "SHORT")
    entry_price: float
    entry_time: str
    entry_ts: float
    tp1: float
    sl: float
    size_usd: float
    units: float
    leverage: float = 1.0
    status: str = "OPEN"
    user_id: str = "default"


@dataclass
class PaperTradeHistory:
    id: str
    symbol: str
    display_symbol: str
    action: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    exit_ts: float
    exit_reason: str  # "TP1_HIT", "SL_HIT", "MANUAL_CLOSE"
    pnl_usd: float
    pnl_pct: float
    size_usd: float
    duration_seconds: float
    date_str: str  # "YYYY-MM-DD" in UTC
    leverage: float = 1.0
    user_id: str = "default"


@dataclass
class UserPaperState:
    settings: PaperSettings
    positions: dict[str, PaperPosition]
    history: list[PaperTradeHistory]

    def __init__(
        self,
        settings: PaperSettings | None = None,
        positions: dict[str, PaperPosition] | None = None,
        history: list[PaperTradeHistory] | None = None,
    ) -> None:
        self.settings = settings or PaperSettings()
        self.positions = positions or {}
        self.history = history or []


class PaperStore:
    def __init__(self) -> None:
        self.users: dict[str, UserPaperState] = {}

    @staticmethod
    def _norm_user_id(user_id: str | int | None) -> str:
        if user_id is None:
            return "default"
        s = str(user_id).strip().strip("<>\"'").lstrip("@").strip()
        return s if s else "default"

    def get_user_state(self, user_id: str | int | None = None) -> UserPaperState:
        uid = self._norm_user_id(user_id)
        if uid not in self.users:
            self.users[uid] = UserPaperState()
        return self.users[uid]

    # Backward compatibility properties for single-user legacy access
    @property
    def settings(self) -> PaperSettings:
        return self.get_user_state("default").settings

    @settings.setter
    def settings(self, val: PaperSettings) -> None:
        self.get_user_state("default").settings = val

    @property
    def positions(self) -> dict[str, PaperPosition]:
        return self.get_user_state("default").positions

    @positions.setter
    def positions(self, val: dict[str, PaperPosition]) -> None:
        self.get_user_state("default").positions = val

    @property
    def history(self) -> list[PaperTradeHistory]:
        return self.get_user_state("default").history

    @history.setter
    def history(self, val: list[PaperTradeHistory]) -> None:
        self.get_user_state("default").history = val

    def to_dict(self) -> dict:
        users_dict = {}
        for uid, ustate in self.users.items():
            users_dict[uid] = {
                "settings": asdict(ustate.settings),
                "positions": {pid: asdict(pos) for pid, pos in ustate.positions.items()},
                "history": [asdict(h) for h in ustate.history],
            }
        default_state = self.get_user_state("default")
        return {
            "users": users_dict,
            "settings": asdict(default_state.settings),
            "positions": {pid: asdict(pos) for pid, pos in default_state.positions.items()},
            "history": [asdict(h) for h in default_state.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PaperStore":
        store = cls()
        admin_id = str(os.getenv("TELEGRAM_CHAT_ID", "6686703329")).strip() or "default"

        # 1. Load users if present in multi-tenant schema
        users_data = data.get("users", {})
        if isinstance(users_data, dict) and users_data:
            for uid, udict in users_data.items():
                norm_uid = cls._norm_user_id(uid)
                settings_dict = udict.get("settings", {})
                settings = PaperSettings(
                    virtual_balance=float(settings_dict.get("virtual_balance", 10000.0)),
                    trade_size_usd=float(settings_dict.get("trade_size_usd", 1000.0)),
                    leverage=float(settings_dict.get("leverage", 1.0)),
                )
                positions = {}
                for pid, pos_dict in udict.get("positions", {}).items():
                    positions[pid] = PaperPosition(
                        id=pos_dict.get("id", pid),
                        symbol=pos_dict.get("symbol", ""),
                        display_symbol=pos_dict.get("display_symbol", ""),
                        action=pos_dict.get("action", "BUY"),
                        entry_price=float(pos_dict.get("entry_price", 0.0)),
                        entry_time=pos_dict.get("entry_time", ""),
                        entry_ts=float(pos_dict.get("entry_ts", 0.0)),
                        tp1=float(pos_dict.get("tp1", 0.0)),
                        sl=float(pos_dict.get("sl", 0.0)),
                        size_usd=float(pos_dict.get("size_usd", 1000.0)),
                        units=float(pos_dict.get("units", 0.0)),
                        leverage=float(pos_dict.get("leverage", 1.0)),
                        status=pos_dict.get("status", "OPEN"),
                        user_id=pos_dict.get("user_id", norm_uid),
                    )
                history = []
                for h_dict in udict.get("history", []):
                    history.append(
                        PaperTradeHistory(
                            id=h_dict.get("id", ""),
                            symbol=h_dict.get("symbol", ""),
                            display_symbol=h_dict.get("display_symbol", ""),
                            action=h_dict.get("action", "BUY"),
                            entry_price=float(h_dict.get("entry_price", 0.0)),
                            exit_price=float(h_dict.get("exit_price", 0.0)),
                            entry_time=h_dict.get("entry_time", ""),
                            exit_time=h_dict.get("exit_time", ""),
                            exit_ts=float(h_dict.get("exit_ts", 0.0)),
                            exit_reason=h_dict.get("exit_reason", "MANUAL_CLOSE"),
                            pnl_usd=float(h_dict.get("pnl_usd", 0.0)),
                            pnl_pct=float(h_dict.get("pnl_pct", 0.0)),
                            size_usd=float(h_dict.get("size_usd", 1000.0)),
                            duration_seconds=float(h_dict.get("duration_seconds", 0.0)),
                            date_str=h_dict.get("date_str", ""),
                            leverage=float(h_dict.get("leverage", 1.0)),
                            user_id=h_dict.get("user_id", norm_uid),
                        )
                    )
                store.users[norm_uid] = UserPaperState(
                    settings=settings,
                    positions=positions,
                    history=history,
                )

        # 2. Legacy Migration: Only if 'users' was not present in the stored data
        has_legacy_data = not users_data and bool(data.get("positions") or data.get("history") or data.get("settings"))
        if has_legacy_data:
            legacy_settings_dict = data.get("settings", {})
            legacy_settings = PaperSettings(
                virtual_balance=float(legacy_settings_dict.get("virtual_balance", 10000.0)),
                trade_size_usd=float(legacy_settings_dict.get("trade_size_usd", 1000.0)),
                leverage=float(legacy_settings_dict.get("leverage", 1.0)),
            )
            legacy_positions = {}
            for pid, pos_dict in data.get("positions", {}).items():
                legacy_positions[pid] = PaperPosition(
                    id=pos_dict.get("id", pid),
                    symbol=pos_dict.get("symbol", ""),
                    display_symbol=pos_dict.get("display_symbol", ""),
                    action=pos_dict.get("action", "BUY"),
                    entry_price=float(pos_dict.get("entry_price", 0.0)),
                    entry_time=pos_dict.get("entry_time", ""),
                    entry_ts=float(pos_dict.get("entry_ts", 0.0)),
                    tp1=float(pos_dict.get("tp1", 0.0)),
                    sl=float(pos_dict.get("sl", 0.0)),
                    size_usd=float(pos_dict.get("size_usd", 1000.0)),
                    units=float(pos_dict.get("units", 0.0)),
                    leverage=float(pos_dict.get("leverage", 1.0)),
                    status=pos_dict.get("status", "OPEN"),
                    user_id=pos_dict.get("user_id", admin_id),
                )
            legacy_history = []
            for h_dict in data.get("history", []):
                legacy_history.append(
                    PaperTradeHistory(
                        id=h_dict.get("id", ""),
                        symbol=h_dict.get("symbol", ""),
                        display_symbol=h_dict.get("display_symbol", ""),
                        action=h_dict.get("action", "BUY"),
                        entry_price=float(h_dict.get("entry_price", 0.0)),
                        exit_price=float(h_dict.get("exit_price", 0.0)),
                        entry_time=h_dict.get("entry_time", ""),
                        exit_time=h_dict.get("exit_time", ""),
                        exit_ts=float(h_dict.get("exit_ts", 0.0)),
                        exit_reason=h_dict.get("exit_reason", "MANUAL_CLOSE"),
                        pnl_usd=float(h_dict.get("pnl_usd", 0.0)),
                        pnl_pct=float(h_dict.get("pnl_pct", 0.0)),
                        size_usd=float(h_dict.get("size_usd", 1000.0)),
                        duration_seconds=float(h_dict.get("duration_seconds", 0.0)),
                        date_str=h_dict.get("date_str", ""),
                        leverage=float(h_dict.get("leverage", 1.0)),
                        user_id=h_dict.get("user_id", admin_id),
                    )
                )

            if "default" not in store.users:
                store.users["default"] = UserPaperState(
                    settings=legacy_settings,
                    positions=legacy_positions,
                    history=legacy_history,
                )
            if admin_id and admin_id not in store.users:
                admin_positions = {pid: PaperPosition(**asdict(pos)) for pid, pos in legacy_positions.items()}
                for p in admin_positions.values():
                    p.user_id = admin_id
                admin_history = [PaperTradeHistory(**asdict(h)) for h in legacy_history]
                for h in admin_history:
                    h.user_id = admin_id
                store.users[admin_id] = UserPaperState(
                    settings=PaperSettings(**asdict(legacy_settings)),
                    positions=admin_positions,
                    history=admin_history,
                )

        return store


_MEMORY_STORE = PaperStore()


def _load_from_cloud() -> PaperStore | None:
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return None

    try:
        resp = requests.get(
            f"{url}/get/{REDIS_PAPER_KEY}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=2,
        )
        if resp.status_code == 200:
            val = resp.json().get("result")
            if val:
                data = json.loads(val)
                return PaperStore.from_dict(data)
    except Exception as exc:
        print(f"[PaperCloud] Error loading from Upstash Redis: {exc}")
    return None


def _save_to_cloud(store: PaperStore) -> bool:
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return False

    try:
        payload = json.dumps(store.to_dict())
        resp = requests.post(
            f"{url}/set/{REDIS_PAPER_KEY}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
            data=payload,
            timeout=2,
        )
        return resp.status_code == 200
    except Exception as exc:
        print(f"[PaperCloud] Error saving to Upstash Redis: {exc}")
        return False


def _load_paper_store() -> PaperStore:
    global _MEMORY_STORE
    # 1. Try Upstash Redis
    cloud_store = _load_from_cloud()
    if cloud_store is not None:
        _MEMORY_STORE = cloud_store
        return _MEMORY_STORE

    # 2. Try Local File
    path = _get_local_store_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                _MEMORY_STORE = PaperStore.from_dict(data)
                return _MEMORY_STORE
        except Exception:
            pass

    return _MEMORY_STORE


def _save_paper_store(store: PaperStore) -> None:
    global _MEMORY_STORE
    _MEMORY_STORE = store

    # 1. Save to cloud
    _save_to_cloud(store)

    # 2. Save locally
    try:
        path = _get_local_store_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store.to_dict(), f, indent=2)
    except Exception as exc:
        print(f"[PaperStore] Local save error: {exc}")


def set_virtual_balance(new_balance: float, user_id: str | int | None = None) -> tuple[bool, str]:
    if new_balance <= 0:
        return False, "❌ Virtual balance must be greater than $0."

    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    ustate.settings.virtual_balance = round(new_balance, 2)
    _save_paper_store(store)
    return True, f"✅ <b>Virtual Balance Updated:</b> <code>${ustate.settings.virtual_balance:,.2f}</code>"


def set_trade_size(new_size: float, user_id: str | int | None = None) -> tuple[bool, str]:
    if new_size <= 0:
        return False, "❌ Trade size must be greater than $0."

    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    ustate.settings.trade_size_usd = round(new_size, 2)
    _save_paper_store(store)
    return True, f"✅ <b>Default Trade Size Updated:</b> <code>${ustate.settings.trade_size_usd:,.2f}</code> per trade"


def set_leverage(new_leverage: float, user_id: str | int | None = None) -> tuple[bool, str]:
    if new_leverage < 1.0 or new_leverage > 125.0:
        return False, "❌ Leverage must be between 1x and 125x (e.g. <code>/setleverage 10x</code>)."

    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    ustate.settings.leverage = round(new_leverage, 1)
    _save_paper_store(store)
    lev_str = f"{ustate.settings.leverage:g}x"
    buying_power = ustate.settings.trade_size_usd * ustate.settings.leverage
    return True, (
        f"⚙️ <b>Leverage Multiplier Updated!</b>\n\n"
        f"• <b>New Leverage:</b> <code>{lev_str}</code>\n"
        f"• <b>Margin Per Trade:</b> <code>${ustate.settings.trade_size_usd:,.2f}</code>\n"
        f"• <b>Total Buying Power:</b> <code>${buying_power:,.2f}</code>\n\n"
        f"<i>All newly entered paper trades will now execute at <b>{lev_str}</b> leverage.</i>"
    )


def get_paper_settings(user_id: str | int | None = None) -> dict:
    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    return asdict(ustate.settings)


def reset_trade_history(user_id: str | int | None = None) -> tuple[bool, str]:
    """
    Clears all closed trade history and resets all-time/daily PnL statistics for the user.
    Leaves active open positions and current virtual balance unchanged.
    """
    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    closed_count = len(ustate.history)
    ustate.history = []
    _save_paper_store(store)
    return True, (
        f"🧹 <b>Trade History &amp; PnL Reset!</b>\n\n"
        f"• Cleared <b>{closed_count}</b> closed trade record(s).\n"
        f"• Daily and All-Time PnL metrics have been reset to <b>$0.00</b>.\n"
        f"• <b>Current Balance:</b> <code>${ustate.settings.virtual_balance:,.2f}</code>\n"
        f"• <b>Active Positions:</b> <code>{len(ustate.positions)}</code> running."
    )


def reset_paper_account(starting_balance: float = 10000.0, user_id: str | int | None = None) -> tuple[bool, str]:
    """
    Performs a complete factory reset of the paper trading account for the user:
    - Closes/clears all open positions
    - Clears all trade history & PnL
    - Resets virtual balance to starting_balance (default $10,000)
    """
    if starting_balance <= 0:
        return False, "❌ Starting balance must be greater than $0."

    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    pos_count = len(ustate.positions)
    hist_count = len(ustate.history)

    ustate.positions = {}
    ustate.history = []
    ustate.settings = PaperSettings(
        virtual_balance=round(starting_balance, 2),
        trade_size_usd=1000.0,
        leverage=1.0,
    )
    _save_paper_store(store)

    return True, (
        f"🔄 <b>Paper Trading Account Fully Reset!</b>\n\n"
        f"• <b>Virtual Balance:</b> <code>${ustate.settings.virtual_balance:,.2f}</code>\n"
        f"• <b>Default Trade Margin:</b> <code>$1,000.00</code>\n"
        f"• <b>Leverage:</b> <code>1x</code> (Spot/Unleveraged)\n"
        f"• <b>Active Positions:</b> Cleared (<b>{pos_count}</b> removed)\n"
        f"• <b>Trade History:</b> Wiped (<b>{hist_count}</b> records removed)\n"
        f"• <b>PnL:</b> Reset to <b>$0.00</b>\n\n"
        f"<i>Ready for fresh forward testing! Use <code>/menu</code> or scans (<code>/c1</code>, <code>/m1</code>, <code>/f1</code>) to start.</i>"
    )


def open_paper_trade(
    symbol: str,
    action: str,
    entry_price: float,
    sl: float,
    tp1: float,
    size_usd: float | None = None,
    leverage: float | None = None,
    user_id: str | int | None = None,
    order_type: str = "MARKET",
    current_price: float | None = None,
) -> tuple[bool, str, dict | None]:
    """
    Opens a simulated paper trade with exact TP1 and SL limits and leverage for the given user.
    Supports both instant MARKET orders and PENDING_LIMIT orders waiting for pullback.
    """
    symbol = normalize_symbol(symbol)
    store = _load_paper_store()
    uid = store._norm_user_id(user_id)
    ustate = store.get_user_state(uid)

    clean_action = action.upper().strip()
    if clean_action in ("BUY", "LONG"):
        clean_action = "BUY"
    elif clean_action in ("SELL", "SHORT"):
        clean_action = "SELL"
    else:
        return False, f"❌ Invalid action '{action}'. Must be BUY or SELL.", None

    if entry_price <= 0 or sl <= 0 or tp1 <= 0:
        return False, "❌ Invalid price levels (Entry, SL, and TP1 must be > 0).", None

    # Check for existing open trade on the same symbol for THIS user
    for pos in ustate.positions.values():
        if pos.symbol == symbol:
            status_desc = "Active position" if pos.status == "OPEN" else "Pending limit order"
            return (
                False,
                f"⚠️ {status_desc} already exists for <b>{pos.display_symbol}</b> (Entry: <code>{_fmt_price(pos.entry_price)}</code>).\n"
                f"Close it first with <code>/close {pos.display_symbol}</code> before entering a new one.",
                None,
            )

    trade_size = size_usd or ustate.settings.trade_size_usd
    trade_lev = leverage or ustate.settings.leverage
    notional_size = trade_size * trade_lev
    units = notional_size / entry_price
    display_sym = DISPLAY_NAMES.get(symbol, symbol.replace("_", "/"))

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ts = now_utc.timestamp()
    time_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    pos_id = f"pos_{uid}_{int(now_ts * 1000)}_{symbol.lower()}"

    # Determine order status: if LIMIT and not yet filled, mark PENDING_LIMIT
    is_pending = False
    if order_type.upper() == "LIMIT":
        if clean_action == "BUY" and current_price is not None and current_price <= entry_price:
            pos_status = "OPEN"
        elif clean_action == "SELL" and current_price is not None and current_price >= entry_price:
            pos_status = "OPEN"
        else:
            pos_status = "PENDING_LIMIT"
            is_pending = True
    else:
        pos_status = "OPEN"

    position = PaperPosition(
        id=pos_id,
        symbol=symbol,
        display_symbol=display_sym,
        action=clean_action,
        entry_price=round(entry_price, 8),
        entry_time=time_str,
        entry_ts=now_ts,
        tp1=round(tp1, 8),
        sl=round(sl, 8),
        size_usd=round(trade_size, 2),
        units=units,
        leverage=trade_lev,
        status=pos_status,
        user_id=uid,
    )

    ustate.positions[pos_id] = position
    _save_paper_store(store)

    dir_emoji = "🟢 LONG" if clean_action == "BUY" else "🔴 SHORT"
    lev_text = f"{trade_lev:g}x"

    if is_pending:
        msg = (
            f"🚀 <b>Paper Trade Entered! (Pending Limit Order)</b>\n\n"
            f"• <b>Asset:</b> <code>{display_sym}</code> ({dir_emoji} LIMIT)\n"
            f"• <b>Leverage:</b> <code>{lev_text}</code>\n"
            f"• <b>Limit Entry:</b> <code>{_fmt_price(entry_price)}</code>\n"
            f"• <b>Current Market:</b> <code>{_fmt_price(current_price or entry_price)}</code>\n"
            f"• <b>Position Margin:</b> <code>${trade_size:,.2f}</code> (Notional: <code>${notional_size:,.2f}</code>)\n"
            f"• <b>Take Profit (TP1):</b> <code>{_fmt_price(tp1)}</code>\n"
            f"• <b>Stop Loss (SL):</b> <code>{_fmt_price(sl)}</code>\n"
            f"• <b>Status:</b> ⏳ Waiting for market pullback to trigger fill...\n\n"
            f"<i>Order will auto-fill on retest, or auto-cancel if TP1 is hit first.</i>"
        )
    else:
        msg = (
            f"🚀 <b>Paper Trade Entered!</b>\n\n"
            f"• <b>Asset:</b> <code>{display_sym}</code> ({dir_emoji})\n"
            f"• <b>Leverage:</b> <code>{lev_text}</code>\n"
            f"• <b>Entry Price:</b> <code>{_fmt_price(entry_price)}</code>\n"
            f"• <b>Position Margin:</b> <code>${trade_size:,.2f}</code> (Notional: <code>${notional_size:,.2f}</code>)\n"
            f"• <b>Take Profit (TP1):</b> <code>{_fmt_price(tp1)}</code>\n"
            f"• <b>Stop Loss (SL):</b> <code>{_fmt_price(sl)}</code>\n"
            f"• <b>Rule:</b> Pure 1:1 TP1/SL execution (Zero Break-Even adjustment)\n\n"
            f"<i>Track live status anytime with <code>/status</code></i>"
        )

    return True, msg, asdict(position)


def close_paper_trade(
    pos_id: str,
    exit_price: float,
    reason: str = "MANUAL_CLOSE",
    user_id: str | int | None = None,
) -> tuple[bool, str, dict | None]:
    """
    Closes an open paper position, calculates realized PnL with leverage, updates virtual balance,
    and archives the trade into history for the owning user.
    """
    store = _load_paper_store()

    target_ustate = None
    target_uid = None
    position = None

    if user_id is not None:
        target_uid = store._norm_user_id(user_id)
        target_ustate = store.get_user_state(target_uid)
        position = target_ustate.positions.get(pos_id)
        if not position:
            norm_sym = normalize_symbol(pos_id)
            for pid, p in target_ustate.positions.items():
                if (
                    p.symbol.lower() == pos_id.lower()
                    or p.symbol.lower() == norm_sym.lower()
                    or p.display_symbol.lower().replace("/", "_") == pos_id.lower().replace("/", "_")
                ):
                    position = p
                    pos_id = pid
                    break
    else:
        # Search across all users
        for uid, ustate in store.users.items():
            p = ustate.positions.get(pos_id)
            if p:
                position = p
                pos_id = p.id
                target_ustate = ustate
                target_uid = uid
                break
            norm_sym = normalize_symbol(pos_id)
            for pid, p2 in ustate.positions.items():
                if (
                    p2.symbol.lower() == pos_id.lower()
                    or p2.symbol.lower() == norm_sym.lower()
                    or p2.display_symbol.lower().replace("/", "_") == pos_id.lower().replace("/", "_")
                ):
                    position = p2
                    pos_id = pid
                    target_ustate = ustate
                    target_uid = uid
                    break
            if position:
                break

    if not position or not target_ustate:
        return False, f"❌ No open position found for ID or symbol '{pos_id}'.", None

    if exit_price <= 0:
        return False, "❌ Invalid exit price.", None

    if getattr(position, "status", "OPEN") == "PENDING_LIMIT":
        del target_ustate.positions[pos_id]
        _save_paper_store(store)
        return True, f"✅ Cancelled pending limit order for <b>{position.display_symbol}</b>.", None

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    exit_ts = now_utc.timestamp()
    exit_time_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    date_str = now_utc.strftime("%Y-%m-%d")

    duration = max(1.0, exit_ts - position.entry_ts)

    # Calculate PnL with leverage
    if position.action == "BUY":
        pnl_usd = position.units * (exit_price - position.entry_price)
    else:  # SELL
        pnl_usd = position.units * (position.entry_price - exit_price)

    pnl_pct = (pnl_usd / position.size_usd) * 100.0 if position.size_usd > 0 else 0.0

    pnl_usd = round(pnl_usd, 2)
    pnl_pct = round(pnl_pct, 2)

    # Update balance for owning user
    target_ustate.settings.virtual_balance = round(target_ustate.settings.virtual_balance + pnl_usd, 2)

    # Archive to user history
    trade_hist = PaperTradeHistory(
        id=position.id,
        symbol=position.symbol,
        display_symbol=position.display_symbol,
        action=position.action,
        entry_price=position.entry_price,
        exit_price=round(exit_price, 8),
        entry_time=position.entry_time,
        exit_time=exit_time_str,
        exit_ts=exit_ts,
        exit_reason=reason,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        size_usd=position.size_usd,
        duration_seconds=duration,
        date_str=date_str,
        leverage=position.leverage,
        user_id=target_uid or "default",
    )

    del target_ustate.positions[pos_id]
    target_ustate.history.append(trade_hist)
    _save_paper_store(store)

    pnl_emoji = "🟢" if pnl_usd >= 0 else "🔴"
    pnl_sign = "+" if pnl_usd >= 0 else ""
    lev_str = f" ({position.leverage:g}x)" if position.leverage > 1.0 else ""

    reason_labels = {
        "TP1_HIT": "🎯 Take Profit 1 Reached",
        "SL_HIT": "🛑 Stop Loss Hit",
        "MANUAL_CLOSE": "✋ Manually Closed",
    }
    reason_text = reason_labels.get(reason, reason)

    msg = (
        f"{pnl_emoji} <b>Paper Trade Closed ({reason_text})</b>\n\n"
        f"• <b>Asset:</b> <code>{position.display_symbol}</code> ({position.action}{lev_str})\n"
        f"• <b>Entry Price:</b> <code>{_fmt_price(position.entry_price)}</code>\n"
        f"• <b>Exit Price:</b> <code>{_fmt_price(exit_price)}</code>\n"
        f"• <b>Realized ROE / PnL:</b> <b>{pnl_sign}${pnl_usd:,.2f}</b> ({pnl_sign}{pnl_pct:.2f}%)\n"
        f"• <b>New Balance:</b> <code>${target_ustate.settings.virtual_balance:,.2f}</code>\n"
        f"• <b>Duration:</b> <code>{int(duration // 60)}m {int(duration % 60)}s</code>"
    )

    return True, msg, asdict(trade_hist)


import concurrent.futures


def check_open_trades(user_id: str | int | None = None) -> list[str]:
    """
    Checks real-time market prices against active open trades.
    If a trade reached TP1 or SL, immediately closes the trade (zero Break-Even adjustment),
    updates the owner's account, and delivers an alert directly to the owning user.
    """
    # Skip live network calls in automated test suite unless mocked
    if os.getenv("PYTEST_CURRENT_TEST") and not hasattr(fetch_live_candles, "mock_calls"):
        return []

    store = _load_paper_store()
    positions_to_check: list[PaperPosition] = []

    if user_id is not None:
        uid = store._norm_user_id(user_id)
        ustate = store.get_user_state(uid)
        positions_to_check = list(ustate.positions.values())
    else:
        for ustate in store.users.values():
            positions_to_check.extend(list(ustate.positions.values()))

    if not positions_to_check:
        return []

    alerts: list[str] = []

    # Fetch live candles concurrently in parallel
    sym_dfs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(positions_to_check))) as executor:
        future_to_pos = {
            executor.submit(fetch_live_candles, pos.symbol): pos
            for pos in positions_to_check
        }
        for future in concurrent.futures.as_completed(future_to_pos):
            pos = future_to_pos[future]
            try:
                sym_dfs[pos.id] = future.result()
            except Exception as exc:
                print(f"[PaperCheck] Error checking {pos.symbol}: {exc}")

    for pos in positions_to_check:
        df = sym_dfs.get(pos.id)
        if df is None or df.empty:
            continue

        last_row = df.iloc[-1]
        curr_close = float(last_row["close"])
        high_price = float(last_row.get("high", curr_close))
        low_price = float(last_row.get("low", curr_close))

        if getattr(pos, "status", "OPEN") == "PENDING_LIMIT":
            # 1. Check if price touched the limit entry (FILLED)
            is_filled = False
            if pos.action == "BUY" and (low_price <= pos.entry_price or curr_close <= pos.entry_price):
                is_filled = True
            elif pos.action == "SELL" and (high_price >= pos.entry_price or curr_close >= pos.entry_price):
                is_filled = True

            if is_filled:
                pos.status = "OPEN"
                _save_paper_store(store)
                dir_emoji = "🟢 LONG" if pos.action == "BUY" else "🔴 SHORT"
                fill_msg = (
                    f"🔔 <b>LIMIT ORDER FILLED!</b>\n\n"
                    f"• <b>Asset:</b> <code>{pos.display_symbol}</code> ({dir_emoji})\n"
                    f"• <b>Filled Price:</b> <code>{_fmt_price(pos.entry_price)}</code>\n"
                    f"• <b>Take Profit (TP1):</b> <code>{_fmt_price(pos.tp1)}</code>\n"
                    f"• <b>Stop Loss (SL):</b> <code>{_fmt_price(pos.sl)}</code>\n"
                    f"• <b>Status:</b> ⚡ Position is now LIVE and tracking!"
                )
                alerts.append(fill_msg)
                if not os.getenv("PYTEST_CURRENT_TEST"):
                    try:
                        from bot.telegram import send_telegram_message
                        target_chat = pos.user_id if (pos.user_id and pos.user_id != "default") else None
                        send_telegram_message(fill_msg, chat_id=target_chat)
                    except Exception as exc:
                        print(f"[PaperCheck] Error notifying fill for {pos.user_id}: {exc}")
                continue

            # 2. Check if price touched TP1 before filling (CANCELLED / MISSED)
            is_cancelled = False
            if pos.action == "BUY" and (high_price >= pos.tp1 or curr_close >= pos.tp1):
                is_cancelled = True
            elif pos.action == "SELL" and (low_price <= pos.tp1 or curr_close <= pos.tp1):
                is_cancelled = True

            if is_cancelled:
                uid = store._norm_user_id(pos.user_id)
                ustate = store.get_user_state(uid)
                ustate.positions.pop(pos.id, None)
                _save_paper_store(store)
                cancel_msg = (
                    f"🚫 <b>PENDING LIMIT CANCELLED</b>\n\n"
                    f"• <b>Asset:</b> <code>{pos.display_symbol}</code> ({pos.action} LIMIT)\n"
                    f"• <b>Reason:</b> Price reached Take Profit (<code>{_fmt_price(pos.tp1)}</code>) before filling limit entry (<code>{_fmt_price(pos.entry_price)}</code>).\n"
                    f"• <b>Action:</b> Stale order safely cleared to protect capital."
                )
                alerts.append(cancel_msg)
                if not os.getenv("PYTEST_CURRENT_TEST"):
                    try:
                        from bot.telegram import send_telegram_message
                        target_chat = pos.user_id if (pos.user_id and pos.user_id != "default") else None
                        send_telegram_message(cancel_msg, chat_id=target_chat)
                    except Exception as exc:
                        print(f"[PaperCheck] Error notifying cancellation for {pos.user_id}: {exc}")
                continue

            # Still waiting for fill
            continue

        target_reason = None
        target_exit = None

        # Long evaluation
        if pos.action == "BUY":
            # Check TP1 first
            if high_price >= pos.tp1 or curr_close >= pos.tp1:
                target_reason = "TP1_HIT"
                target_exit = pos.tp1
            elif low_price <= pos.sl or curr_close <= pos.sl:
                target_reason = "SL_HIT"
                target_exit = pos.sl

        # Short evaluation
        elif pos.action == "SELL":
            # Check TP1 first (for shorts, TP1 is lower)
            if low_price <= pos.tp1 or curr_close <= pos.tp1:
                target_reason = "TP1_HIT"
                target_exit = pos.tp1
            elif high_price >= pos.sl or curr_close >= pos.sl:
                target_reason = "SL_HIT"
                target_exit = pos.sl

        if target_reason and target_exit:
            ok, alert_msg, _ = close_paper_trade(pos.id, target_exit, reason=target_reason, user_id=pos.user_id)
            if ok and alert_msg:
                alerts.append(alert_msg)
                # Dispatch alert to owning user
                if not os.getenv("PYTEST_CURRENT_TEST"):
                    try:
                        from bot.telegram import send_telegram_message
                        target_chat = pos.user_id if (pos.user_id and pos.user_id != "default") else None
                        send_telegram_message(alert_msg, chat_id=target_chat)
                    except Exception as exc:
                        print(f"[PaperCheck] Error notifying {pos.user_id}: {exc}")

    return alerts


def get_status_report(user_id: str | int | None = None) -> tuple[str, dict | None]:
    """
    Builds the live /status report showing Virtual Balance, open positions,
    live unrealized PnL, progress towards TP1/SL, and quick manual close buttons for the specified user.
    """
    check_open_trades(user_id=user_id)
    store = _load_paper_store()
    ustate = store.get_user_state(user_id)

    balance = ustate.settings.virtual_balance
    trade_size = ustate.settings.trade_size_usd
    leverage = ustate.settings.leverage
    buying_power = trade_size * leverage
    open_count = len(ustate.positions)

    lines = [
        "📊 <b>PAPER TRADING LIVE DASHBOARD</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💼 <b>Virtual Balance:</b> <code>${balance:,.2f}</code>",
        f"⚙️ <b>Trade Margin:</b> <code>${trade_size:,.2f}</code> | <b>Leverage:</b> <code>{leverage:g}x</code> (Buying Power: <code>${buying_power:,.2f}</code>)",
        f"⚡ <b>Active Positions:</b> <code>{open_count}</code>",
        "",
    ]

    close_buttons = []

    if open_count == 0:
        lines.append("<i>No active paper trades currently running.</i>")
        lines.append("<i>Run <code>/c1</code>, <code>/c2</code>, <code>/m1</code>, <code>/m2</code> or <code>/f1</code> to scan and enter setups!</i>")
    else:
        total_unrealized_usd = 0.0

        for idx, (pid, pos) in enumerate(ustate.positions.items(), 1):
            curr_price = pos.entry_price
            try:
                # In tests, avoid unmocked calls
                if not os.getenv("PYTEST_CURRENT_TEST") or hasattr(fetch_live_candles, "mock_calls"):
                    df = fetch_live_candles(pos.symbol)
                    if not df.empty:
                        curr_price = float(df.iloc[-1]["close"])
            except Exception:
                pass

            lev_tag = f" ({pos.leverage:g}x)" if pos.leverage > 1.0 else ""
            if getattr(pos, "status", "OPEN") == "PENDING_LIMIT":
                side_str = f"⏳ PENDING {pos.action} LIMIT{lev_tag}"
                lines.append(f"<b>{idx}. {pos.display_symbol}</b> ({side_str})")
                lines.append(f"   • Limit Entry: <code>{_fmt_price(pos.entry_price)}</code> | Current: <code>{_fmt_price(curr_price)}</code>")
                lines.append(f"   • TP1: <code>{_fmt_price(pos.tp1)}</code> | SL: <code>{_fmt_price(pos.sl)}</code>")
                lines.append(f"   • Status: ⏳ <b>Pending Fill</b> (Waiting for pullback)")
                lines.append("")
                close_buttons.append({
                    "text": f"❌ Cancel {pos.display_symbol}",
                    "callback_data": f"paper_close:{pid}",
                })
            else:
                if pos.action == "BUY":
                    pnl_usd = pos.units * (curr_price - pos.entry_price)
                else:
                    pnl_usd = pos.units * (pos.entry_price - curr_price)

                pnl_pct = (pnl_usd / pos.size_usd) * 100.0 if pos.size_usd > 0 else 0.0
                side_str = f"🟢 LONG{lev_tag}" if pos.action == "BUY" else f"🔴 SHORT{lev_tag}"

                total_unrealized_usd += pnl_usd
                pnl_sign = "+" if pnl_usd >= 0 else ""
                pnl_icon = "🟢" if pnl_usd >= 0 else "🔴"

                lines.append(f"<b>{idx}. {pos.display_symbol}</b> ({side_str})")
                lines.append(f"   • Entry: <code>{_fmt_price(pos.entry_price)}</code> | Current: <code>{_fmt_price(curr_price)}</code>")
                lines.append(f"   • TP1: <code>{_fmt_price(pos.tp1)}</code> | SL: <code>{_fmt_price(pos.sl)}</code>")
                lines.append(f"   • Unrealized ROE: {pnl_icon} <b>{pnl_sign}${pnl_usd:,.2f}</b> ({pnl_sign}{pnl_pct:.2f}%)")
                lines.append("")

                close_buttons.append({
                    "text": f"❌ Close {pos.display_symbol}",
                    "callback_data": f"paper_close:{pid}",
                })

        tot_sign = "+" if total_unrealized_usd >= 0 else ""
        tot_icon = "🟢" if total_unrealized_usd >= 0 else "🔴"
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📈 <b>Total Floating PnL:</b> {tot_icon} <b>{tot_sign}${total_unrealized_usd:,.2f}</b>")

    # Overall historical stats for this user
    history = ustate.history
    if history:
        total_closed = len(history)
        wins = [h for h in history if h.pnl_usd > 0]
        losses = [h for h in history if h.pnl_usd <= 0]
        win_rate = (len(wins) / total_closed) * 100.0 if total_closed > 0 else 0.0
        total_pnl = sum(h.pnl_usd for h in history)
        tot_hist_sign = "+" if total_pnl >= 0 else ""

        lines.append("")
        lines.append("🏆 <b>All-Time History:</b>")
        lines.append(f"• Total Trades: <code>{total_closed}</code> | Win Rate: <code>{win_rate:.1f}%</code> ({len(wins)}W / {len(losses)}L)")
        lines.append(f"• Net Closed PnL: <b>{tot_hist_sign}${total_pnl:,.2f}</b>")

    keyboard = None
    if close_buttons:
        # Arrange close buttons in rows of 2
        rows = [close_buttons[i : i + 2] for i in range(0, len(close_buttons), 2)]
        rows.append([{"text": "🔄 Refresh Status", "callback_data": "/status"}])
        keyboard = {"inline_keyboard": rows}

    return "\n".join(lines), keyboard


def get_daily_summary_report(target_date: str | None = None, user_id: str | int | None = None) -> str:
    """
    Builds the end-of-day summary report aggregating closed trades for the day for the specified user:
    - Full Day's Realized PnL ($ and %)
    - Win Rate & Trade Count
    - Best Performing Pairs (Top Winners)
    - Worst Performing Pairs (Top Losers)
    - Open positions still running
    """
    check_open_trades(user_id=user_id)
    store = _load_paper_store()
    ustate = store.get_user_state(user_id)

    today_str = target_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # Filter trades closed today for this user
    day_trades = [h for h in ustate.history if h.date_str == today_str]

    lines = [
        f"📅 <b>END-OF-DAY PERFORMANCE SUMMARY</b>",
        f"<i>Date: {today_str} (UTC)</i>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not day_trades:
        lines.append("ℹ️ <i>No trades were closed on this day yet.</i>")
        lines.append("")
        lines.append(f"💼 <b>Current Virtual Balance:</b> <code>${ustate.settings.virtual_balance:,.2f}</code>")
        lines.append(f"⚡ <b>Active Positions Running:</b> <code>{len(ustate.positions)}</code>")
        return "\n".join(lines)

    total_closed = len(day_trades)
    wins = [t for t in day_trades if t.pnl_usd > 0]
    losses = [t for t in day_trades if t.pnl_usd <= 0]
    win_rate = (len(wins) / total_closed) * 100.0 if total_closed > 0 else 0.0

    day_pnl_usd = sum(t.pnl_usd for t in day_trades)
    pnl_sign = "+" if day_pnl_usd >= 0 else ""
    pnl_icon = "🟢" if day_pnl_usd >= 0 else "🔴"

    # Group PnL by symbol/pair to find best and worst pairs
    pair_pnl: dict[str, float] = {}
    for t in day_trades:
        sym = t.display_symbol
        pair_pnl[sym] = pair_pnl.get(sym, 0.0) + t.pnl_usd

    sorted_pairs = sorted(pair_pnl.items(), key=lambda x: x[1], reverse=True)
    best_pairs = [p for p in sorted_pairs if p[1] > 0]
    worst_pairs = [p for p in sorted_pairs if p[1] <= 0]

    lines.append(f"💰 <b>Daily Realized PnL:</b> {pnl_icon} <b>{pnl_sign}${day_pnl_usd:,.2f}</b>")
    lines.append(f"💼 <b>Current Balance:</b> <code>${ustate.settings.virtual_balance:,.2f}</code>")
    lines.append(f"🎯 <b>Win Rate:</b> <code>{win_rate:.1f}%</code> (<b>{len(wins)}</b> Wins / <b>{len(losses)}</b> Losses)")
    lines.append(f"📊 <b>Total Trades Completed:</b> <code>{total_closed}</code>")
    lines.append("")

    # Best performing pairs
    lines.append("🌟 <b>Top Performing Pairs:</b>")
    if best_pairs:
        for sym, pnl in best_pairs[:3]:
            lines.append(f"   • <b>{sym}</b>: 🟢 <b>+${pnl:,.2f}</b>")
    else:
        lines.append("   • <i>None with positive PnL today</i>")

    lines.append("")

    # Worst performing pairs
    lines.append("⚠️ <b>Underperforming Pairs:</b>")
    if worst_pairs:
        for sym, pnl in worst_pairs[-3:]:
            lines.append(f"   • <b>{sym}</b>: 🔴 <b>-${abs(pnl):,.2f}</b>")
    else:
        lines.append("   • <i>None with negative PnL today (100% Green!)</i>")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⚡ <b>Positions Still Running:</b> <code>{len(ustate.positions)}</code>")
    lines.append("<i>Send <code>/status</code> to inspect active positions.</i>")

    return "\n".join(lines)


def get_trade_history_report(limit: int = 10, user_id: str | int | None = None) -> str:
    """Returns a list of the last N closed trades for the specified user."""
    store = _load_paper_store()
    ustate = store.get_user_state(user_id)
    history = ustate.history[-limit:]

    if not history:
        return "ℹ️ <i>No paper trade history recorded yet.</i>"

    lines = [
        f"📜 <b>Recent Paper Trade History (Last {len(history)}):</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for h in reversed(history):
        pnl_icon = "🟢" if h.pnl_usd >= 0 else "🔴"
        pnl_sign = "+" if h.pnl_usd >= 0 else ""
        lines.append(
            f"{pnl_icon} <b>{h.display_symbol}</b> ({h.action}) — {h.exit_reason}\n"
            f"   • Entry: <code>{_fmt_price(h.entry_price)}</code> $\\rightarrow$ Exit: <code>{_fmt_price(h.exit_price)}</code>\n"
            f"   • PnL: <b>{pnl_sign}${h.pnl_usd:,.2f}</b> ({pnl_sign}{h.pnl_pct:.2f}%)\n"
            f"   • Date: <code>{h.exit_time}</code>"
        )
        lines.append("")

    return "\n".join(lines).strip()


def close_all_open_trades(user_id: str | int | None = None) -> tuple[int, str]:
    """Closes active open positions for the given user (or all users if user_id is None)."""
    store = _load_paper_store()
    positions_to_close: list[tuple[str, PaperPosition, str]] = []

    if user_id is not None:
        uid = store._norm_user_id(user_id)
        ustate = store.get_user_state(uid)
        for pid, pos in list(ustate.positions.items()):
            positions_to_close.append((pid, pos, uid))
    else:
        for uid, ustate in store.users.items():
            for pid, pos in list(ustate.positions.items()):
                positions_to_close.append((pid, pos, uid))

    if not positions_to_close:
        return 0, "ℹ️ No open positions to close."

    count = 0
    total_pnl = 0.0
    for pid, pos, uid in positions_to_close:
        curr_price = pos.entry_price
        if not os.getenv("PYTEST_CURRENT_TEST") or hasattr(fetch_live_candles, "mock_calls"):
            try:
                df = fetch_live_candles(pos.symbol)
                if not df.empty:
                    curr_price = float(df.iloc[-1]["close"])
            except Exception:
                pass

        ok, _, report_data = close_paper_trade(pid, curr_price, reason="MANUAL_CLOSE", user_id=uid)
        if ok and report_data:
            count += 1
            total_pnl += report_data.get("pnl_usd", 0.0)

    pnl_sign = "+" if total_pnl >= 0 else ""
    return count, f"✅ <b>Closed {count} active positions.</b> Total Realized PnL: <b>{pnl_sign}${total_pnl:,.2f}</b>"

