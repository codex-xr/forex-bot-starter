import json
import os
from http.server import BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv

from bot.telegram_commands import handle_message
from bot.autopilot import autopilot
from bot.session_bot import ALL_WATCHLIST


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            update = json.loads(post_data.decode("utf-8"))
            if "message" in update:
                handle_message(update["message"])
        except Exception as exc:
            print(f"[Vercel Webhook] Error: {exc}")

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path.endswith("/set_webhook"):
            load_dotenv()
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if not token:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "TELEGRAM_BOT_TOKEN missing"}).encode("utf-8"))
                return

            host = self.headers.get("Host", "")
            proto = self.headers.get("X-Forwarded-Proto", "https")
            webhook_url = f"{proto}://{host}/api/webhook"

            try:
                tg_res = requests.post(
                    f"https://api.telegram.org/bot{token}/setWebhook",
                    json={"url": webhook_url},
                    timeout=15,
                ).json()
            except Exception as exc:
                tg_res = {"error": str(exc)}

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "message": "Webhook setup attempted",
                "registered_url": webhook_url,
                "telegram_response": tg_res,
            }, indent=2).encode("utf-8"))
            return

        if path.endswith("/cron"):
            alerts = []
            for symbol in ALL_WATCHLIST:
                try:
                    if autopilot.scan_single_symbol(symbol):
                        alerts.append(symbol)
                except Exception as exc:
                    print(f"[Cron Scan] Error on {symbol}: {exc}")

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "success",
                "symbols_scanned": len(ALL_WATCHLIST),
                "alerts_triggered": alerts,
            }, indent=2).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Forex & Crypto Telegram Bot</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; line-height: 1.6;">
            <h2>🤖 Forex, Crypto & Memecoin Signal Bot</h2>
            <p>Your Telegram Bot serverless backend is <b>Active & Ready to accept Webhook POST requests</b>.</p>
            <hr>
            <h3>Quick Actions:</h3>
            <ul>
                <li><a href="/api/set_webhook"><b>👉 Set / Activate Telegram Webhook</b></a></li>
                <li><a href="/api/cron"><b>👉 Trigger Manual Auto-Pilot Scan</b></a></li>
            </ul>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))
