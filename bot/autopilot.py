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
from bot.symbols import DISPLAY_NAMES
from bot.telegram import send_telegram_message

REDIS_AUTOPILOT_KEY = "autopilot_store_data"


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
    ) -> None:
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.scan_delay_seconds = scan_delay_seconds
        self.broadcast_to_vips = broadcast_to_vips
        self.is_active = True
        self.last_alerts: dict[str, float] = {}
        self.alert_history: list[dict] = []
        self._last_sync_ts: float = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

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

    def scan_single_symbol(self, symbol: str) -> bool:
        """Scans a single symbol and sends a live call if a valid setup is detected. Returns True if sent."""
        self._sync_from_cloud()
        if not self.is_active:
            return False

        try:
            prices = fetch_live_candles(symbol)
        except Exception:
            return False

        report = analyze_setup(symbol, prices, min_confidence=self.min_confidence)

        # Ignore WAIT states completely
        if report.action not in ("BUY", "SELL"):
            return False

        # Anti-spam cooldown check
        if self.is_on_cooldown(symbol, report.action):
            return False

        disp = DISPLAY_NAMES.get(symbol, symbol.replace("_", "/"))
        alert_msg = (
            f"🚨 <b>AUTO-PILOT SIGNAL DETECTED</b> 🚨\n\n"
            f"{report.to_message()}\n\n"
            f"<i>Live Auto-Pilot Call (≥{self.min_confidence}% Confidence) • Tap below to enter.</i>"
        )
        reply_markup = {
            "inline_keyboard": [
                [{"text": f"🚀 Enter {disp} ({report.action})", "callback_data": f"paper_enter:{symbol}:{report.action}"}],
                [{"text": "📊 Live Positions (/status)", "callback_data": "/status"}],
            ]
        }

        # Record cooldown immediately before fan-out to prevent concurrent duplicate calls
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

    def run_cycle(self) -> list[str]:
        """Runs one fast concurrent sweep across all monitored crypto & memecoin symbols."""
        self._sync_from_cloud(force=True)
        if not self.is_active:
            return []

        triggered: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_sym = {
                executor.submit(self.scan_single_symbol, symbol): symbol
                for symbol in ALL_WATCHLIST
            }
            for future in concurrent.futures.as_completed(future_to_sym):
                symbol = future_to_sym[future]
                try:
                    if future.result():
                        triggered.append(symbol)
                except Exception as exc:
                    print(f"[AutoPilot] Error scanning {symbol}: {exc}")
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
        state = "🟢 ACTIVE (Running 24/7)" if self.is_active else "🔴 PAUSED"
        lines = [
            "🤖 <b>Auto-Pilot Live Calls Status</b>",
            "",
            f"• <b>Status:</b> {state}",
            f"• <b>Markets:</b> <code>Crypto & Memecoins ({len(ALL_WATCHLIST)} pairs)</code>",
            f"• <b>Min Confidence:</b> <code>{self.min_confidence}%</code> (A+ Setups Only)",
            "• <b>Recipients:</b> <code>Admin + Active Crypto/Bundle VIPs</code>",
            "• <b>Scan Interval:</b> <code>Every ~45 seconds (&lt;1 min)</code>",
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


def main() -> None:
    print(f"[AutoPilot] Starting dedicated 24/7 live-call worker (min_confidence={autopilot.min_confidence}%)...")
    autopilot.run_loop()


if __name__ == "__main__":
    main()
