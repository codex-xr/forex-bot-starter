import json
import os
from http.server import BaseHTTPRequestHandler
from bot.telegram_commands import handle_message


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
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h3>Telegram Bot Webhook is live on Vercel!</h3>")
