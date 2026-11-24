import concurrent.futures
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.access_control import get_crypto_signal_recipients
from bot.market_data import fetch_live_candles
from bot.session_bot import ALL_WATCHLIST
from bot.signal_engine import analyze_setup
from bot.symbols import DISPLAY_NAMES, normalize_symbol
from bot.telegram import send_telegram_message

REDIS_AUTOPILOT_KEY = "autopilot_store_data"
_SIGNAL_SNAPSHOTS: dict[str, dict] = {}


def _normalize_schedule_mode(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    if m in ("golden", "golden_wat", "wat", "hours", "best"):
        return "golden_wat"
    return "24/7"


def record_signal_snapshot(
    symbol: str,
    action: str,
    entry: float | None,
    tp1: float | None = None,
    sl: float | None = None,
    confidence: int | None = None,
) -> None:
    """Stores the original signal price and timestamp so late 1-tap Enter clicks can warn against chasing."""
    if entry is None or entry <= 0 or action not in ("BUY", "SELL"):
        return
    norm_sym = normalize_symbol(symbol)
    key = f"{norm_sym}_{action}"
    snap = {
        "entry": float(entry),
        "tp1": float(tp1) if tp1 else None,
        "sl": float(sl) if sl else None,
        "confidence": confidence,
        "ts": time.time(),
    }
    _SIGNAL_SNAPSHOTS[key] = snap
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if url and token:
        try:
            requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=["SET", f"signal_snap:{key}", json.dumps(snap), "EX", "3600"],
                timeout=1.5,
            )
        except Exception:
            pass


def get_signal_snapshot(symbol: str, action: str) -> dict | None:
    """Fetches a recent signal snapshot (< 60m old) from memory or Upstash Redis."""
    norm_sym = normalize_symbol(symbol)
    key = f"{norm_sym}_{action}"
    now = time.time()
    snap = _SIGNAL_SNAPSHOTS.get(key)
    if snap and (now - float(snap.get("ts") or 0.0)) <= 3600.0:
        return snap
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return None
    try:
        res = requests.get(
            f"{url}/get/signal_snap:{key}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=1.5,
        )
        if res.status_code == 200:
            raw = res.json().get("result")
            if isinstance(raw, str) and raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and (now - float(parsed.get("ts") or 0.0)) <= 3600.0:
                    _SIGNAL_SNAPSHOTS[key] = parsed
                    return parsed
    except Exception:
        pass
    return None


class AutoPilot:
    """
    Background 24/7 Auto-Pilot Scanner.
    Silently scans all crypto & memecoin watchlist symbols and triggers a live call
    ONLY when a high-conviction setup (BUY/SELL >= min_confidence) is detected.
    State & cooldowns sync with Upstash Redis so Railway worker and Vercel commands share state.
    """

    def __init__(
        self,
        min_confidence: int = 75,
        cooldown_seconds: int = 3600,
        scan_delay_seconds: float = 45.0,
        broadcast_to_vips: bool = False,
        schedule_mode: str = "24/7",
        max_alerts_per_cycle: int = 1,
    ) -> None:
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.scan_delay_seconds = scan_delay_seconds
        self.broadcast_to_vips = broadcast_to_vips
        self.schedule_mode = _normalize_schedule_mode(schedule_mode)
        # Crypto pairs are highly correlated.  A scan that finds several BUYs (or
        # SELLs) is usually one market thesis, not several independent bets.
        # Keep the broadcast basket deliberately small to avoid stacking risk.
        self.max_alerts_per_cycle = max(1, int(max_alerts_per_cycle))
        self.is_active = True
        self.last_alerts: dict[str, float] = {}
        self.alert_history: list[dict] = []
        self._last_sync_ts: float = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def is_within_scan_window(self, now_utc: datetime | None = None) -> bool:
        """Returns True if Auto-Pilot is allowed to hunt new signals at this hour."""
        if self.schedule_mode != "golden_wat":
            return True
        dt = now_utc or datetime.now(timezone.utc)
        wat_hour = (dt.hour + 1) % 24
        return (8 <= wat_hour <= 12) or (16 <= wat_hour <= 17)

    def set_schedule_mode(self, mode: str) -> str:
        self.schedule_mode = _normalize_schedule_mode(mode)
        self._save_to_cloud()
        return self.schedule_mode

    def _sync_from_cloud(self, force: bool = False) -> None:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        now = time.time()
        if not force and (now - self._last_sync_ts) < 5.0:
            return
        url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if not url or not token:
            return
        try:
            res = requests.get(
                f"{url}/get/{REDIS_AUTOPILOT_KEY}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=2.0,
            )
            self._last_sync_ts = now
            if res.status_code == 200:
                val = res.json().get("result")
                if isinstance(val, str) and val:
                    data = json.loads(val)
                    with self._lock:
                        if "is_active" in data:
                            self.is_active = bool(data["is_active"])
                        if "schedule_mode" in data:
                            self.schedule_mode = _normalize_schedule_mode(data["schedule_mode"])
                        if isinstance(data.get("last_alerts"), dict):
                            cutoff = now - (self.cooldown_seconds * 2)
                            self.last_alerts = {
                                k: float(v)
                                for k, v in data["last_alerts"].items()
                                if float(v) >= cutoff
                            }
                        if isinstance(data.get("alert_history"), list):
                            self.alert_history = data["alert_history"][-20:]
        except Exception as exc:
            print(f"[AutoPilot] Cloud sync error: {exc}")

    def _save_to_cloud(self) -> None:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if not url or not token:
            return
        try:
            with self._lock:
                cutoff = time.time() - (self.cooldown_seconds * 2)
                pruned_alerts = {k: v for k, v in self.last_alerts.items() if v >= cutoff}
                payload = {
                    "is_active": self.is_active,
                    "schedule_mode": self.schedule_mode,
                    "last_alerts": pruned_alerts,
                    "alert_history": self.alert_history[-20:],
                }
            requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=["SET", REDIS_AUTOPILOT_KEY, json.dumps(payload)],
                timeout=2.0,
            )
            self._last_sync_ts = time.time()
        except Exception as exc:
            print(f"[AutoPilot] Cloud save error: {exc}")

    def _claim_cloud_alert_lock(self, symbol: str, action: str) -> bool:
        """Atomically claims a cloud alert lock so simultaneous triggers never double-send the same signal."""
        if os.getenv("PYTEST_CURRENT_TEST"):
            return True
        url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if not url or not token:
            return True
        try:
            res = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=["SET", f"autopilot_lock:{symbol}_{action}", str(time.time()), "NX", "EX", str(self.cooldown_seconds)],
                timeout=2.0,
            )
            if res.status_code == 200:
                return res.json().get("result") == "OK"
        except Exception:
            pass
        return True

    def is_on_cooldown(self, symbol: str, action: str) -> bool:
        self._sync_from_cloud()
        key = f"{symbol}_{action}"
        with self._lock:
            last_time = self.last_alerts.get(key)
        if last_time is None:
            return False
        return (time.time() - last_time) < self.cooldown_seconds

    def record_alert(self, symbol: str, action: str, confidence: int, reason: str) -> None:
        key = f"{symbol}_{action}"
        now = time.time()
        with self._lock:
            self.last_alerts[key] = now
            self.alert_history.append({
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "reason": reason,
                "time": datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
            if len(self.alert_history) > 20:
                self.alert_history.pop(0)
        self._save_to_cloud()

    def _evaluate_symbol(self, symbol: str):
        """Return a qualifying signal without sending it, or ``None`` when no setup qualifies."""
        self._sync_from_cloud()
        if not self.is_active or not self.is_within_scan_window():
            return None

        try:
            prices = fetch_live_candles(symbol)
        except Exception:
            return None

        report = analyze_setup(symbol, prices, min_confidence=self.min_confidence)
        if report.action not in ("BUY", "SELL"):
            return None

        return report

    def _send_signal_alert(self, symbol: str, report) -> bool:
        """Send one already-qualified signal after duplicate/cooldown checks."""
        if report.action not in ("BUY", "SELL"):
            return False

        # Anti-spam cooldown check & atomic cloud lock against overlapping triggers
        if self.is_on_cooldown(symbol, report.action):
            return False
        if not self._claim_cloud_alert_lock(symbol, report.action):
            return False

        disp = DISPLAY_NAMES.get(symbol, symbol.replace("_", "/"))
        alert_msg = (
            f"🚨 <b>AUTO-PILOT SIGNAL DETECTED</b> 🚨\n\n"
            f"{report.to_message()}\n\n"
            f"<i>Live Auto-Pilot Call (≥{self.min_confidence}% Confidence) • Tap below to enter.</i>"
        )
        reply_markup = {
            "inline_keyboard": [
                [{"text": f"🚀 Enter {disp} ({report.action})", "callback_data": f"paper_enter:{symbol}:{report.action}:{report.confidence}"}],
                [{"text": "📊 Live Positions (/status)", "callback_data": "/status"}],
            ]
        }

        # Record cooldown & signal snapshot immediately before fan-out
        record_signal_snapshot(
            symbol,
            report.action,
            report.entry,
            report.tp1 or report.take_profit,
            report.stop_loss,
            report.confidence,
        )
        self.record_alert(symbol, report.action, report.confidence, report.reason)

        if self.broadcast_to_vips:
            recipients = get_crypto_signal_recipients()
            if recipients:
                for cid in recipients:
                    try:
                        send_telegram_message(alert_msg, chat_id=cid, reply_markup=reply_markup)
                        time.sleep(0.04)
                    except Exception as exc:
                        print(f"[AutoPilot] Failed sending alert for {symbol} to {cid}: {exc}")
            else:
                send_telegram_message(alert_msg, reply_markup=reply_markup)
        else:
            send_telegram_message(alert_msg, reply_markup=reply_markup)

        print(f"[AutoPilot] Live call sent for {symbol} ({report.action} at {report.confidence}% confidence)")
        return True

    def scan_single_symbol(self, symbol: str) -> bool:
        """Scans a single symbol and sends a live call if a valid setup is detected. Returns True if sent."""
        report = self._evaluate_symbol(symbol)
        if report is None:
            return False
        return self._send_signal_alert(symbol, report)

    def run_cycle(self) -> list[str]:
        """Runs one fast concurrent sweep across all monitored crypto & memecoin symbols."""
        self._sync_from_cloud(force=True)
        if not self.is_active or not self.is_within_scan_window():
            return []

        try:
            from bot.news_engine import fetch_latest_news
            fetch_latest_news(limit=25)
        except Exception:
            pass

        candidates: list[tuple[str, object]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            future_to_sym = {
                executor.submit(self._evaluate_symbol, symbol): symbol
                for symbol in ALL_WATCHLIST
            }
            for future in concurrent.futures.as_completed(future_to_sym):
                symbol = future_to_sym[future]
                try:
                    report = future.result()
                    if report is not None:
                        candidates.append((symbol, report))
                except Exception as exc:
                    print(f"[AutoPilot] Error scanning {symbol}: {exc}")

        # Rank the entire market before broadcasting.  This prevents a single
        # broad move from producing a basket of correlated calls (for example,
        # several altcoin BUYs during the same downtrend).  Ties follow the
        # stable watchlist order, so alert selection is deterministic.
        watchlist_order = {symbol: index for index, symbol in enumerate(ALL_WATCHLIST)}
        candidates.sort(key=lambda item: (-item[1].confidence, watchlist_order[item[0]]))
        triggered: list[str] = []
        for symbol, report in candidates[:self.max_alerts_per_cycle]:
            if self._send_signal_alert(symbol, report):
                triggered.append(symbol)
        return triggered

    def run_loop(self) -> None:
        print(f"[AutoPilot] Engine started (min_confidence={self.min_confidence}%, {len(ALL_WATCHLIST)} pairs). Scanning 24/7...")
        self._sync_from_cloud(force=True)
        while not self._stop_event.is_set():
            cycle_start = time.time()
            try:
                from bot.paper_engine import check_open_trades
                from bot.poly_paper_engine import check_open_poly_trades
                from bot.access_control import process_due_broadcasts
                check_open_trades()
                check_open_poly_trades()
                process_due_broadcasts()
            except Exception as exc:
                print(f"[AutoPilot] Error checking open trades/broadcasts: {exc}")

            self._sync_from_cloud(force=True)
            if self.is_active:
                try:
                    self.run_cycle()
                except Exception as exc:
                    print(f"[AutoPilot] Cycle error: {exc}")

            elapsed = time.time() - cycle_start
            wait_secs = max(5.0, self.scan_delay_seconds - elapsed)
            self._stop_event.wait(timeout=wait_secs)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_loop, daemon=True, name="AutoPilotThread")
        self._thread.start()

    def stop(self) -> None:
        self.is_active = False
        self._save_to_cloud()

    def resume(self) -> None:
        self.is_active = True
        self._save_to_cloud()

    def get_status_text(self) -> str:
        self._sync_from_cloud(force=True)
        if not self.is_active:
            state = "🔴 PAUSED"
        elif self.schedule_mode == "golden_wat":
            in_win = self.is_within_scan_window()
            win_tag = "Inside Window" if in_win else "Waiting for Window"
            state = f"🌟 GOLDEN HOURS ONLY ({win_tag})"
        else:
            state = "🟢 ACTIVE (Running 24/7)"
        mode_label = (
            "Golden Hours Only (8AM–1PM & 4PM–6PM WAT)"
            if self.schedule_mode == "golden_wat"
            else "24/7 All Hours"
        )
        lines = [
            "🤖 <b>Auto-Pilot Live Calls Status</b>",
            "",
            f"• <b>Status:</b> {state}",
            f"• <b>Scan Hours:</b> <code>{mode_label}</code>",
            f"• <b>Markets:</b> <code>Crypto & Memecoins ({len(ALL_WATCHLIST)} pairs)</code>",
            f"• <b>Min Confidence:</b> <code>{self.min_confidence}%</code> (A+ Setups Only)",
            f"• <b>Risk Basket:</b> <code>Top {self.max_alerts_per_cycle} signal per scan</code>",
            "• <b>Recipients:</b> <code>Admin + Active Crypto/Bundle VIPs</code>",
            f"• <b>Signal Cooldown:</b> <code>{self.cooldown_seconds // 60} minutes</code>",
            "",
        ]

        if self.alert_history:
            lines.append("<b>Recent Live Calls Triggered:</b>")
            for h in reversed(self.alert_history[-5:]):
                lines.append(f"• <b>{h['symbol']}</b>: {h['action']} (<code>{h['confidence']}%</code>) at {h['time']}")
        else:
            lines.append("<i>No recent auto-pilot calls yet. Hunting 24/7 for ≥75% setups...</i>")

        return "\n".join(lines)


# Global singleton instance (75%+ Crypto & Memecoin live calls -> Admin + Crypto VIPs)
autopilot = AutoPilot(min_confidence=75, broadcast_to_vips=True)


def run_cron_cycle(run_autopilot: bool = True) -> dict:
    """Checks open paper trades, runs due broadcasts, and optionally runs an AutoPilot scan cycle."""
    paper_trade_alerts = []
    poly_trade_alerts = []
    executed_broadcasts = []
    alerts = []

    try:
        from bot.paper_engine import check_open_trades
        paper_trade_alerts = check_open_trades()
    except Exception as exc:
        print(f"[Cron PaperTrade] Error: {exc}")

    try:
        from bot.poly_paper_engine import check_open_poly_trades
        poly_trade_alerts = check_open_poly_trades()
    except Exception as exc:
        print(f"[Cron PolyTrade] Error: {exc}")

    try:
        from bot.access_control import process_due_broadcasts
        executed_broadcasts = process_due_broadcasts()
    except Exception as exc:
        print(f"[Cron Broadcast] Error: {exc}")

    if run_autopilot and autopilot:
        try:
            alerts = autopilot.run_cycle()
        except Exception as exc:
            print(f"[Cron Scan] AutoPilot cycle error: {exc}")

    will_scan = bool(run_autopilot and autopilot and autopilot.is_active and autopilot.is_within_scan_window())
    mode_val = getattr(autopilot, "schedule_mode", "24/7") if autopilot else "24/7"
    if mode_val not in ("24/7", "golden_wat"):
        mode_val = "24/7"

    return {
        "ok": True,
        "status": "success",
        "symbols_scanned": len(ALL_WATCHLIST) if will_scan else 0,
        "schedule_mode": mode_val,
        "within_scan_window": bool(autopilot.is_within_scan_window()) if autopilot else True,
        "alerts_triggered": alerts,
        "paper_trade_alerts": len(paper_trade_alerts),
        "poly_trade_alerts": len(poly_trade_alerts),
        "broadcasts_executed": executed_broadcasts,
    }


def main() -> None:
    print(f"[AutoPilot] Starting dedicated 24/7 live-call worker (min_confidence={autopilot.min_confidence}%)...")
    autopilot.run_loop()


if __name__ == "__main__":
    main()

