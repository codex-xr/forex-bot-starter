import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import requests
from dotenv import load_dotenv

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from bot.telegram_commands import handle_message, handle_callback_query
    from bot.autopilot import run_cron_cycle
    from bot.telegram import register_telegram_commands
except Exception as import_err:
    print(f"[Vercel Startup Error]: {import_err}")
    handle_message = None
    handle_callback_query = None
    run_cron_cycle = None
    _IMPORT_ERROR = traceback.format_exc()
else:
    _IMPORT_ERROR = None


class handler(BaseHTTPRequestHandler):
    def _is_cron_request(self) -> bool:
        path = self.path.split("?")[0].rstrip("/")
        orig_path = (
            self.headers.get("x-vercel-original-path")
            or self.headers.get("x-matched-path")
            or self.headers.get("x-invoke-path")
            or ""
        ).split("?")[0].rstrip("/")
        return path.endswith("/cron") or orig_path.endswith("/cron") or "cron=" in self.path

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        resp_payload = {"ok": True}

        try:
            update = json.loads(post_data.decode("utf-8")) if post_data.strip() else {}
            action = update.get("action") if isinstance(update, dict) else None
            if action in ("check_trades", "run_cron", "run_autopilot", "cron") and run_cron_cycle:
                resp_payload = run_cron_cycle(run_autopilot=(action != "check_trades"))
            elif self._is_cron_request() and run_cron_cycle:
                resp_payload = run_cron_cycle(run_autopilot=("mode=trades" not in self.path))
            elif isinstance(update, dict) and "callback_query" in update:
                cb = update["callback_query"]
                qid = cb.get("id")
                if qid:
                    resp_payload = {
                        "method": "answerCallbackQuery",
                        "callback_query_id": qid,
                    }
                if handle_callback_query:
                    handle_callback_query(cb)
            elif isinstance(update, dict) and "message" in update and handle_message:
                handle_message(update["message"])
        except Exception as exc:
            print(f"[Vercel Webhook] Error: {exc}")

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp_payload).encode("utf-8"))

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

        if self._is_cron_request() and run_cron_cycle:
            res = run_cron_cycle(run_autopilot=("mode=trades" not in self.path))
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res, indent=2).encode("utf-8"))
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
