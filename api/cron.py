import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.autopilot import run_cron_cycle


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Triggered by Cron / 24/7 Trade Watcher to check open paper trades, process broadcasts, and run AutoPilot."""
        res = run_cron_cycle(run_autopilot=("mode=trades" not in self.path))
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(res, indent=2).encode("utf-8"))

    def do_POST(self):
        """Supports POST cron triggers from GitHub Actions and external schedulers."""
        self.do_GET()

