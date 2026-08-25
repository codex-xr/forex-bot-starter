import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Forex & Crypto Telegram Bot</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; line-height: 1.6;">
            <h2>🤖 Forex, Crypto & Memecoin Signal Bot</h2>
            <p>Your Telegram Bot serverless backend is <b>Active & Running on Vercel</b>.</p>
            <hr>
            <h3>Quick Links:</h3>
            <ul>
                <li><a href="/api/set_webhook"><b>👉 Click here to Set / Activate Telegram Webhook</b></a></li>
                <li><a href="/api/cron"><b>👉 Trigger Manual Auto-Pilot Scan</b></a></li>
            </ul>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))
