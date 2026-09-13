from datetime import datetime, timezone
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
    from bot.autopilot import autopilot
    from bot.session_bot import ALL_WATCHLIST
    from bot.telegram import register_telegram_commands
except Exception as import_err:
    print(f"[Vercel Startup Error]: {import_err}")
    handle_message = None
    handle_callback_query = None
    autopilot = None
    ALL_WATCHLIST = []
    _IMPORT_ERROR = traceback.format_exc()
else:
    _IMPORT_ERROR = None


def _log_webhook_event(event_type: str, details: dict) -> None:
    try:
        url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if not url or not token:
            return
        entry = {
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "event": event_type,
            "details": details,
        }
        headers = {"Authorization": f"Bearer {token}"}
        requests.post(url, headers=headers, json=["LPUSH", "webhook_debug_logs", json.dumps(entry)], timeout=1.5)
        requests.post(url, headers=headers, json=["LTRIM", "webhook_debug_logs", 0, 29], timeout=1.0)
    except Exception:
        pass


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        resp_payload = {"ok": True}

        try:
            update = json.loads(post_data.decode("utf-8"))
            if "callback_query" in update:
                cb = update["callback_query"]
                qid = cb.get("id")
                cdata = cb.get("data")
                cuser = cb.get("from", {})
                cchat = cb.get("message", {}).get("chat", {}).get("id")
                _log_webhook_event("callback_query_received", {
                    "id": qid,
                    "data": cdata,
                    "from_id": cuser.get("id"),
                    "username": cuser.get("username"),
                    "chat_id": cchat,
                })
                if qid:
                    resp_payload = {
                        "method": "answerCallbackQuery",
                        "callback_query_id": qid,
                    }
                if handle_callback_query:
                    handle_callback_query(cb)
                    _log_webhook_event("callback_query_handled", {"id": qid, "data": cdata})
            elif "message" in update and handle_message:
                msg = update["message"]
                _log_webhook_event("message_received", {
                    "text": msg.get("text"),
                    "from_id": msg.get("from", {}).get("id"),
                    "username": msg.get("from", {}).get("username"),
                    "chat_id": msg.get("chat", {}).get("id"),
                })
                handle_message(msg)
                _log_webhook_event("message_handled", {"text": msg.get("text")})
        except Exception as exc:
            err_str = traceback.format_exc()
            print(f"[Vercel Webhook] Error: {exc}")
            _log_webhook_event("webhook_error", {"error": str(exc), "traceback": err_str})

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp_payload).encode("utf-8"))

    def do_GET(self):
        if "debug" in self.path or "logs" in self.path:
            load_dotenv()
            logs = []
            try:
                url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
                token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
                if url and token:
                    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=["LRANGE", "webhook_debug_logs", 0, 29], timeout=3)
                    if r.status_code == 200:
                        raw_items = r.json().get("result", [])
                        logs = [json.loads(x) if isinstance(x, str) else x for x in raw_items]
            except Exception as e:
                logs = [{"error": str(e)}]

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"total_logs": len(logs), "logs": logs}, indent=2).encode("utf-8"))
            return

        if "set_webhook" in self.path:
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
                    json={
                        "url": webhook_url,
                        "allowed_updates": ["message", "callback_query"],
                        "drop_pending_updates": True,
                    },
                    timeout=15,
                ).json()
                cmds_res = register_telegram_commands()
            except Exception as exc:
                tg_res = {"error": str(exc)}
                cmds_res = {"error": str(exc)}

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "message": "Webhook & Menu Commands setup attempted",
                "registered_url": webhook_url,
                "telegram_webhook_response": tg_res,
                "telegram_commands_response": cmds_res,
            }, indent=2).encode("utf-8"))
            return

        if "cron" in self.path:
            # 1. Process any due scheduled broadcasts
            executed_broadcasts = []
            try:
                from bot.access_control import process_due_broadcasts
                executed_broadcasts = process_due_broadcasts()
            except Exception as exc:
                print(f"[Cron Broadcast] Error: {exc}")

            # 2. Check open paper trades (trigger TP1/SL closures and deliver to owning users)
            paper_trade_alerts = []
            try:
                from bot.paper_engine import check_open_trades
                paper_trade_alerts = check_open_trades()
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
            self.wfile.write(json.dumps({
                "status": "success",
                "symbols_scanned": len(ALL_WATCHLIST),
                "alerts_triggered": alerts,
                "paper_trade_alerts": len(paper_trade_alerts),
                "broadcasts_executed": executed_broadcasts,
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
