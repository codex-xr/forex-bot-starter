import json
import os
from http.server import BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """1-Click Telegram Webhook Registration."""
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "TELEGRAM_BOT_TOKEN environment variable is missing"}).encode("utf-8"))
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
