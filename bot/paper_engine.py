import datetime
import html
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
import requests

from bot.market_data import fetch_live_candles
from bot.symbols import DISPLAY_NAMES

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
    status: str = "OPEN"


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


class PaperStore:
    def __init__(self) -> None:
        self.settings: PaperSettings = PaperSettings()
        self.positions: dict[str, PaperPosition] = {}
        self.history: list[PaperTradeHistory] = []

    def to_dict(self) -> dict:
        return {
            "settings": asdict(self.settings),
            "positions": {pid: asdict(pos) for pid, pos in self.positions.items()},
            "history": [asdict(h) for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PaperStore":
        store = cls()
        settings_data = data.get("settings", {})
        store.settings = PaperSettings(
            virtual_balance=float(settings_data.get("virtual_balance", 10000.0)),
            trade_size_usd=float(settings_data.get("trade_size_usd", 1000.0)),
        )

        positions_data = data.get("positions", {})
        for pid, pos_dict in positions_data.items():
            store.positions[pid] = PaperPosition(
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
                status=pos_dict.get("status", "OPEN"),
            )

        history_data = data.get("history", [])
        for h_dict in history_data:
            store.history.append(
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
                )
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
            timeout=4,
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
            timeout=4,
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


def set_virtual_balance(new_balance: float) -> tuple[bool, str]:
    if new_balance <= 0:
        return False, "❌ Virtual balance must be greater than $0."

    store = _load_paper_store()
    store.settings.virtual_balance = round(new_balance, 2)
    _save_paper_store(store)
    return True, f"✅ <b>Virtual Balance Updated:</b> <code>${store.settings.virtual_balance:,.2f}</code>"


def set_trade_size(new_size: float) -> tuple[bool, str]:
    if new_size <= 0:
        return False, "❌ Trade size must be greater than $0."

    store = _load_paper_store()
    store.settings.trade_size_usd = round(new_size, 2)
    _save_paper_store(store)
    return True, f"✅ <b>Default Trade Size Updated:</b> <code>${store.settings.trade_size_usd:,.2f}</code> per trade"


def get_paper_settings() -> dict:
    store = _load_paper_store()
    return asdict(store.settings)


def open_paper_trade(
    symbol: str,
    action: str,
    entry_price: float,
    sl: float,
    tp1: float,
    size_usd: float | None = None,
) -> tuple[bool, str, dict | None]:
    """
    Opens a simulated paper trade with exact TP1 and SL limits.
    """
    store = _load_paper_store()

    clean_action = action.upper()
    if clean_action in ("BUY", "LONG"):
        clean_action = "BUY"
    elif clean_action in ("SELL", "SHORT"):
        clean_action = "SELL"
    else:
        return False, f"❌ Invalid action '{action}'. Must be BUY or SELL.", None

    if entry_price <= 0 or sl <= 0 or tp1 <= 0:
        return False, "❌ Invalid price levels (Entry, SL, and TP1 must be > 0).", None

    # Check for existing open trade on the same symbol
    for pos in store.positions.values():
        if pos.symbol == symbol:
            return (
                False,
                f"⚠️ Active position already exists for <b>{pos.display_symbol}</b> (Entry: <code>{_fmt_price(pos.entry_price)}</code>).\n"
                f"Close it first with <code>/close {pos.display_symbol}</code> before entering a new one.",
                None,
            )

    trade_size = size_usd or store.settings.trade_size_usd
    units = trade_size / entry_price
    display_sym = DISPLAY_NAMES.get(symbol, symbol.replace("_", "/"))

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ts = now_utc.timestamp()
    time_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    pos_id = f"pos_{int(now_ts)}_{symbol.lower()}"

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
        status="OPEN",
    )

    store.positions[pos_id] = position
    _save_paper_store(store)

    dir_emoji = "🟢 LONG" if clean_action == "BUY" else "🔴 SHORT"
    msg = (
        f"🚀 <b>Paper Trade Entered!</b>\n\n"
        f"• <b>Asset:</b> <code>{display_sym}</code> ({dir_emoji})\n"
        f"• <b>Entry Price:</b> <code>{_fmt_price(entry_price)}</code>\n"
        f"• <b>Position Size:</b> <code>${trade_size:,.2f}</code> ({units:.6g} units)\n"
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
) -> tuple[bool, str, dict | None]:
    """
    Closes an open paper position, calculates realized PnL, updates virtual balance,
    and archives the trade into history.
    """
    store = _load_paper_store()

    position = store.positions.get(pos_id)
    if not position:
        # Search by symbol match if pos_id is symbol name
        for pid, p in store.positions.items():
            if p.symbol.lower() == pos_id.lower() or p.display_symbol.lower().replace("/", "_") == pos_id.lower().replace("/", "_"):
                position = p
                pos_id = pid
                break

    if not position:
        return False, f"❌ No open position found for ID or symbol '{pos_id}'.", None

    if exit_price <= 0:
        return False, "❌ Invalid exit price.", None

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    exit_ts = now_utc.timestamp()
    exit_time_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    date_str = now_utc.strftime("%Y-%m-%d")

    duration = max(1.0, exit_ts - position.entry_ts)

    # Calculate PnL
    if position.action == "BUY":
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100.0
        pnl_usd = position.units * (exit_price - position.entry_price)
    else:  # SELL
        pnl_pct = ((position.entry_price - exit_price) / position.entry_price) * 100.0
        pnl_usd = position.units * (position.entry_price - exit_price)

    pnl_usd = round(pnl_usd, 2)
    pnl_pct = round(pnl_pct, 2)

    # Update balance
    store.settings.virtual_balance = round(store.settings.virtual_balance + pnl_usd, 2)

    # Archive to history
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
    )

    del store.positions[pos_id]
    store.history.append(trade_hist)
    _save_paper_store(store)

    pnl_emoji = "🟢" if pnl_usd >= 0 else "🔴"
    pnl_sign = "+" if pnl_usd >= 0 else ""

    reason_labels = {
        "TP1_HIT": "🎯 Take Profit 1 Reached",
        "SL_HIT": "🛑 Stop Loss Hit",
        "MANUAL_CLOSE": "✋ Manually Closed",
    }
    reason_text = reason_labels.get(reason, reason)

    msg = (
        f"{pnl_emoji} <b>Paper Trade Closed ({reason_text})</b>\n\n"
        f"• <b>Asset:</b> <code>{position.display_symbol}</code> ({position.action})\n"
        f"• <b>Entry Price:</b> <code>{_fmt_price(position.entry_price)}</code>\n"
        f"• <b>Exit Price:</b> <code>{_fmt_price(exit_price)}</code>\n"
        f"• <b>Realized PnL:</b> <b>{pnl_sign}${pnl_usd:,.2f}</b> ({pnl_sign}{pnl_pct:.2f}%)\n"
        f"• <b>New Balance:</b> <code>${store.settings.virtual_balance:,.2f}</code>\n"
        f"• <b>Duration:</b> <code>{int(duration // 60)}m {int(duration % 60)}s</code>"
    )

    return True, msg, asdict(trade_hist)


def check_open_trades() -> list[str]:
    """
    Checks real-time market prices against all active open trades.
    If a trade reached TP1 or SL, immediately closes the trade (zero Break-Even adjustment)
    and generates an alert notification.
    """
    # Skip live network calls in automated test suite unless mocked
    if os.getenv("PYTEST_CURRENT_TEST") and not hasattr(fetch_live_candles, "mock_calls"):
        return []

    store = _load_paper_store()
    if not store.positions:
        return []

    alerts: list[str] = []
    positions_to_check = list(store.positions.values())

    for pos in positions_to_check:
        try:
            df = fetch_live_candles(pos.symbol)
            if df.empty:
                continue

            last_row = df.iloc[-1]
            curr_close = float(last_row["close"])
            high_price = float(last_row.get("high", curr_close))
            low_price = float(last_row.get("low", curr_close))

            # Long evaluation
            if pos.action == "BUY":
                # Check TP1 first
                if high_price >= pos.tp1 or curr_close >= pos.tp1:
                    ok, alert_msg, _ = close_paper_trade(pos.id, pos.tp1, reason="TP1_HIT")
                    if ok:
                        alerts.append(alert_msg)
                    continue

                # Check SL
                if low_price <= pos.sl or curr_close <= pos.sl:
                    ok, alert_msg, _ = close_paper_trade(pos.id, pos.sl, reason="SL_HIT")
                    if ok:
                        alerts.append(alert_msg)
                    continue

            # Short evaluation
            elif pos.action == "SELL":
                # Check TP1 first (for shorts, TP1 is lower)
                if low_price <= pos.tp1 or curr_close <= pos.tp1:
                    ok, alert_msg, _ = close_paper_trade(pos.id, pos.tp1, reason="TP1_HIT")
                    if ok:
                        alerts.append(alert_msg)
                    continue

                # Check SL (for shorts, SL is higher)
                if high_price >= pos.sl or curr_close >= pos.sl:
                    ok, alert_msg, _ = close_paper_trade(pos.id, pos.sl, reason="SL_HIT")
                    if ok:
                        alerts.append(alert_msg)
                    continue

        except Exception as exc:
            print(f"[PaperCheck] Error checking {pos.symbol}: {exc}")

    return alerts


def get_status_report() -> tuple[str, dict | None]:
    """
    Builds the live /status report showing Virtual Balance, open positions,
    live unrealized PnL, progress towards TP1/SL, and quick manual close buttons.
    """
    # First trigger check on open trades
    check_open_trades()
    store = _load_paper_store()

    balance = store.settings.virtual_balance
    trade_size = store.settings.trade_size_usd
    open_count = len(store.positions)

    lines = [
        "📊 <b>PAPER TRADING LIVE DASHBOARD</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💼 <b>Virtual Balance:</b> <code>${balance:,.2f}</code>",
        f"⚙️ <b>Trade Size:</b> <code>${trade_size:,.2f}</code> per entry",
        f"⚡ <b>Active Positions:</b> <code>{open_count}</code>",
        "",
    ]

    close_buttons = []

    if open_count == 0:
        lines.append("<i>No active paper trades currently running.</i>")
        lines.append("<i>Run <code>/c1</code>, <code>/c2</code>, <code>/m1</code>, <code>/m2</code> or <code>/f1</code> to scan and enter setups!</i>")
    else:
        total_unrealized_usd = 0.0

        for idx, (pid, pos) in enumerate(store.positions.items(), 1):
            curr_price = pos.entry_price
            try:
                # In tests, avoid unmocked calls
                if not os.getenv("PYTEST_CURRENT_TEST") or hasattr(fetch_live_candles, "mock_calls"):
                    df = fetch_live_candles(pos.symbol)
                    if not df.empty:
                        curr_price = float(df.iloc[-1]["close"])
            except Exception:
                pass

            if pos.action == "BUY":
                pnl_pct = ((curr_price - pos.entry_price) / pos.entry_price) * 100.0
                pnl_usd = pos.units * (curr_price - pos.entry_price)
                side_str = "🟢 LONG"
            else:
                pnl_pct = ((pos.entry_price - curr_price) / pos.entry_price) * 100.0
                pnl_usd = pos.units * (pos.entry_price - curr_price)
                side_str = "🔴 SHORT"

            total_unrealized_usd += pnl_usd
            pnl_sign = "+" if pnl_usd >= 0 else ""
            pnl_icon = "🟢" if pnl_usd >= 0 else "🔴"

            lines.append(f"<b>{idx}. {pos.display_symbol}</b> ({side_str})")
            lines.append(f"   • Entry: <code>{_fmt_price(pos.entry_price)}</code> | Current: <code>{_fmt_price(curr_price)}</code>")
            lines.append(f"   • TP1: <code>{_fmt_price(pos.tp1)}</code> | SL: <code>{_fmt_price(pos.sl)}</code>")
            lines.append(f"   • Unrealized PnL: {pnl_icon} <b>{pnl_sign}${pnl_usd:,.2f}</b> ({pnl_sign}{pnl_pct:.2f}%)")
            lines.append("")

            close_buttons.append({
                "text": f"❌ Close {pos.display_symbol}",
                "callback_data": f"paper_close:{pid}",
            })

        tot_sign = "+" if total_unrealized_usd >= 0 else ""
        tot_icon = "🟢" if total_unrealized_usd >= 0 else "🔴"
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📈 <b>Total Floating PnL:</b> {tot_icon} <b>{tot_sign}${total_unrealized_usd:,.2f}</b>")

    # Overall historical stats
    history = store.history
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


def get_daily_summary_report(target_date: str | None = None) -> str:
    """
    Builds the end-of-day summary report aggregating closed trades for the day:
    - Full Day's Realized PnL ($ and %)
    - Win Rate & Trade Count
    - Best Performing Pairs (Top Winners)
    - Worst Performing Pairs (Top Losers)
    - Open positions still running
    """
    # First trigger check on open trades
    check_open_trades()
    store = _load_paper_store()

    today_str = target_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # Filter trades closed today
    day_trades = [h for h in store.history if h.date_str == today_str]

    lines = [
        f"📅 <b>END-OF-DAY PERFORMANCE SUMMARY</b>",
        f"<i>Date: {today_str} (UTC)</i>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not day_trades:
        lines.append("ℹ️ <i>No trades were closed on this day yet.</i>")
        lines.append("")
        lines.append(f"💼 <b>Current Virtual Balance:</b> <code>${store.settings.virtual_balance:,.2f}</code>")
        lines.append(f"⚡ <b>Active Positions Running:</b> <code>{len(store.positions)}</code>")
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
    lines.append(f"💼 <b>Current Balance:</b> <code>${store.settings.virtual_balance:,.2f}</code>")
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
    lines.append(f"⚡ <b>Positions Still Running:</b> <code>{len(store.positions)}</code>")
    lines.append("<i>Send <code>/status</code> to inspect active positions.</i>")

    return "\n".join(lines)


def get_trade_history_report(limit: int = 10) -> str:
    """Returns a list of the last N closed trades."""
    store = _load_paper_store()
    history = store.history[-limit:]

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


def close_all_open_trades() -> tuple[int, str]:
    """Closes all active open positions at current market prices."""
    store = _load_paper_store()
    if not store.positions:
        return 0, "ℹ️ No open positions to close."

    count = 0
    total_pnl = 0.0
    for pid, pos in list(store.positions.items()):
        curr_price = pos.entry_price
        try:
            df = fetch_live_candles(pos.symbol)
            if not df.empty:
                curr_price = float(df.iloc[-1]["close"])
        except Exception:
            pass

        ok, _, report_data = close_paper_trade(pid, curr_price, reason="MANUAL_CLOSE")
        if ok and report_data:
            count += 1
            total_pnl += report_data.get("pnl_usd", 0.0)

    pnl_sign = "+" if total_pnl >= 0 else ""
    return count, f"✅ <b>Closed {count} active positions.</b> Total Realized PnL: <b>{pnl_sign}${total_pnl:,.2f}</b>"
