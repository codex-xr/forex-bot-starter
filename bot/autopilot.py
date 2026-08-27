import sys
import threading
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.market_data import fetch_live_candles
from bot.session_bot import ALL_WATCHLIST
from bot.signal_engine import analyze_setup
from bot.telegram import send_telegram_message


class AutoPilot:
    """
    Background 24/7 Auto-Pilot Scanner.
    Silently scans all watchlist symbols and triggers an alert ONLY when
    a single high-conviction trade setup (BUY/SELL) is detected.
    """

    def __init__(
        self,
        min_confidence: int = 65,
        cooldown_seconds: int = 3600,
        scan_delay_seconds: float = 8.0,
    ) -> None:
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.scan_delay_seconds = scan_delay_seconds
        self.is_active = True
        self.last_alerts: dict[str, float] = {}
        self.alert_history: list[dict] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def is_on_cooldown(self, symbol: str, action: str) -> bool:
        key = f"{symbol}_{action}"
        last_time = self.last_alerts.get(key)
        if last_time is None:
            return False
        return (time.time() - last_time) < self.cooldown_seconds

    def record_alert(self, symbol: str, action: str, confidence: int, reason: str) -> None:
        key = f"{symbol}_{action}"
        now = time.time()
        self.last_alerts[key] = now
        self.alert_history.append({
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "time": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
        if len(self.alert_history) > 20:
            self.alert_history.pop(0)

    def scan_single_symbol(self, symbol: str) -> bool:
        """Scans a single symbol and sends an alert if a valid setup is detected. Returns True if alert sent."""
        if not self.is_active:
            return False

        try:
            prices = fetch_live_candles(symbol)
        except Exception as exc:
            # Silent ignore in background
            return False

        report = analyze_setup(symbol, prices, min_confidence=self.min_confidence)

        # Ignore WAIT states completely
        if report.action not in ("BUY", "SELL"):
            return False

        # Anti-spam cooldown check
        if self.is_on_cooldown(symbol, report.action):
            return False

        # Build singular alert message
        alert_msg = (
            f"🚨 <b>AUTO-PILOT SIGNAL DETECTED</b> 🚨\n\n"
            f"{report.to_message()}\n\n"
            f"<i>Auto-Pilot is actively hunting 24/7 across all markets.</i>"
        )

        send_telegram_message(alert_msg)
        self.record_alert(symbol, report.action, report.confidence, report.reason)
        print(f"[AutoPilot] 🎯 Alert sent for {symbol} ({report.action} at {report.confidence}% confidence)")
        return True

    def run_loop(self) -> None:
        print("[AutoPilot] Engine started. Scanning watchlists in background...")
        while not self._stop_event.is_set():
            if not self.is_active:
                time.sleep(5)
                continue

            for symbol in ALL_WATCHLIST:
                if self._stop_event.is_set() or not self.is_active:
                    break

                try:
                    self.scan_single_symbol(symbol)
                except Exception as exc:
                    print(f"[AutoPilot] Error scanning {symbol}: {exc}")

                time.sleep(self.scan_delay_seconds)

            # Rest briefly before next full cycle
            time.sleep(10)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_loop, daemon=True, name="AutoPilotThread")
        self._thread.start()

    def stop(self) -> None:
        self.is_active = False

    def resume(self) -> None:
        self.is_active = True

    def get_status_text(self) -> str:
        state = "🟢 ACTIVE (Running 24/7)" if self.is_active else "🔴 PAUSED"
        lines = [
            "🤖 <b>Auto-Pilot Status</b>",
            "",
            f"• <b>Status:</b> {state}",
            f"• <b>Min Confidence:</b> <code>{self.min_confidence}%</code>",
            f"• <b>Monitored Pairs:</b> <code>{len(ALL_WATCHLIST)} symbols</code>",
            f"• <b>Signal Cooldown:</b> <code>{self.cooldown_seconds // 60} minutes</code>",
            "",
        ]

        if self.alert_history:
            lines.append("<b>Recent Signals Triggered:</b>")
            for h in reversed(self.alert_history[-5:]):
                lines.append(f"• <b>{h['symbol']}</b>: {h['action']} (<code>{h['confidence']}%</code>) at {h['time']}")
        else:
            lines.append("<i>No recent auto-pilot alerts yet. Hunting for high-probability setups...</i>")

        return "\n".join(lines)


# Global singleton instance
autopilot = AutoPilot(min_confidence=65)


def main() -> None:
    autopilot.start_background()
    print("[AutoPilot] Running in standalone mode. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[AutoPilot] Stopping...")
        autopilot._stop_event.set()


if __name__ == "__main__":
    main()
