#!/usr/bin/env python3
"""
Simple HTTP server that exposes launchctl service status as JSON.
Usage: python3 launchctl_status_server.py
"""

import json
import re
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8765

# Services to monitor
SERVICES = [
    {
        "label": "dev.fjorn.bbctl-imessage",  # Update this to your actual service name
        "display_name": "iMessage Bridge",
    },
    {
        # Ollama.app manages its own process, so check the API instead of launchctl
        "label": "Ollama.app :11434",
        "display_name": "Ollama",
        "url": "http://127.0.0.1:11434/api/version",
    },
    {
        "label": "homebrew.mxcl.beszel-agent",
        "display_name": "Beszel Agent",
    }
    # Add more services here as needed
    # {
    #     "label": "com.example.another-service",
    #     "display_name": "Another Service"
    # }
]


def check_launchctl_service(label: str) -> dict:
    """
    Check if a launchctl service is running.
    Returns dict with status information.
    """
    try:
        # Run launchctl list for the specific service
        result = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True, timeout=2
        )

        if result.returncode != 0:
            # Service is not loaded
            return {"running": False, "loaded": False, "status": "not loaded"}

        # Parse the output to get PID and status
        output = result.stdout
        pid = None
        last_exit_status = None

        # Look for PID line
        pid_match = re.search(r'"PID"\s*=\s*(\d+|"-")', output)
        if pid_match:
            pid_str = pid_match.group(1)
            if pid_str != '"-"':
                pid = int(pid_str)

        # Look for LastExitStatus
        exit_match = re.search(r'"LastExitStatus"\s*=\s*(\d+)', output)
        if exit_match:
            last_exit_status = int(exit_match.group(1))

        # Determine if running
        is_running = pid is not None

        return {
            "running": is_running,
            "loaded": True,
            "pid": pid,
            "last_exit_status": last_exit_status,
            "status": "running" if is_running else "loaded but not running",
        }

    except subprocess.TimeoutExpired:
        return {
            "running": False,
            "loaded": False,
            "status": "timeout",
            "error": "Command timed out",
        }
    except Exception as e:
        return {"running": False, "loaded": False, "status": "error", "error": str(e)}


def check_http_service(url: str) -> dict:
    """
    Check if an HTTP endpoint responds with a 2xx status.
    """
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            ok = 200 <= response.status < 300
            return {
                "running": ok,
                "loaded": True,
                "status": "running" if ok else f"http {response.status}",
            }
    except Exception as e:
        return {"running": False, "loaded": False, "status": "unreachable", "error": str(e)}


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            services_status = []

            for service in SERVICES:
                if "url" in service:
                    status = check_http_service(service["url"])
                else:
                    status = check_launchctl_service(service["label"])
                services_status.append(
                    {
                        "label": service["label"],
                        "name": service["display_name"],
                        **status,
                    }
                )

            response = {"services": services_status}

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(response, indent=2).encode())

        elif self.path == "/":
            # Simple HTML page for testing
            html = """
            <html>
            <head><title>Launchctl Status</title></head>
            <body>
                <h1>Launchctl Status Server</h1>
                <p>Visit <a href="/status">/status</a> for JSON output</p>
            </body>
            </html>
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default logging (or customize it)
        pass


if __name__ == "__main__":
    print(f"Starting launchctl status server on http://localhost:{PORT}")
    print(f"Monitoring services: {[s['display_name'] for s in SERVICES]}")
    print(f"Status endpoint: http://localhost:{PORT}/status")

    server = HTTPServer(("127.0.0.1", PORT), StatusHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
