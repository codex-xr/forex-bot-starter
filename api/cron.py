import json
from http.server import BaseHTTPRequestHandler
from bot.autopilot import autopilot
from bot.session_bot import ALL_WATCHLIST
from bot.access_control import process_due_broadcasts
from bot.paper_engine import check_open_trades
from bot.telegram import send_telegram_message


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Triggered by Vercel Cron every 15 minutes to run AutoPilot scan, check paper trades, and process due scheduled broadcasts."""
        # 1. Process due scheduled broadcasts
        executed_broadcasts = []
        try:
            executed_broadcasts = process_due_broadcasts()
        except Exception as exc:
            print(f"[Cron Broadcast] Error: {exc}")

        # 2. Check open paper trades (trigger TP1/SL closures)
        paper_trade_alerts = []
        try:
            paper_trade_alerts = check_open_trades()
            for alert_msg in paper_trade_alerts:
                send_telegram_message(alert_msg)
        except Exception as exc:
            print(f"[Cron PaperTrade] Error: {exc}")

        # 3. Run autopilot symbol scans
        alerts = []
        for symbol in ALL_WATCHLIST:
            try:
                if autopilot and autopilot.scan_single_symbol(symbol):
                    alerts.append(symbol)
            except Exception as exc:
                print(f"[Cron Scan] Error on {symbol}: {exc}")

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        res = {
            "status": "success",
            "symbols_scanned": len(ALL_WATCHLIST),
            "alerts_triggered": alerts,
            "paper_trade_alerts": len(paper_trade_alerts),
            "broadcasts_executed": executed_broadcasts,
        }
        self.wfile.write(json.dumps(res, indent=2).encode("utf-8"))
