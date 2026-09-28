"""A local stand-in for Northwind Retail's ops platform.

One stdlib HTTP server with the endpoints the on-call agent uses: alerts,
metrics, deploys, rollbacks, paging, and the status page. Reads come from an
in-memory world. Every write is appended to an action log (JSON Lines): the
record of what the agent actually did.

The default world has a live incident. Deploy d-4127 went out twelve minutes
ago and checkout-api has been failing since; d-4126, fifty minutes ago, was
healthy. With ``outage=False`` the same world is quiet and the alert is noise.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

SERVICE = "checkout-api"
ALERT_ID = "ALRT-5521"
THRESHOLD = 0.05


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


class World:
    """What the ops platform knows. Timestamps are relative to the moment of the request."""

    def __init__(self, outage: bool = True):
        self.outage = outage

    def alert(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "id": ALERT_ID,
            "service": SERVICE,
            "metric": "error_rate",
            "threshold": THRESHOLD,
            "window": "15m",
            "status": "open",
            "opened_at": _iso(now - timedelta(minutes=9)),
            "summary": f"{SERVICE} error rate above {THRESHOLD:.0%} for 5 minutes",
        }

    def error_rate(self, minutes: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        series = []
        for ago in range(minutes, 0, -1):
            # Checkouts started failing when d-4127 went out, twelve minutes ago.
            failing = self.outage and ago <= 12
            series.append({"at": _iso(now - timedelta(minutes=ago)), "value": 0.18 if failing else 0.004})
        return {"service": SERVICE, "window": f"{minutes}m", "series": series, "as_of": _iso(now)}

    def deploys(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "service": SERVICE,
            "since": "24h",
            "as_of": _iso(now),
            "deploys": [
                {"id": "d-4127", "version": "2026.09.28.3", "author": "mhale",
                 "deployed_at": _iso(now - timedelta(minutes=12))},
                {"id": "d-4126", "version": "2026.09.28.2", "author": "jkoh",
                 "deployed_at": _iso(now - timedelta(minutes=50))},
                {"id": "d-4119", "version": "2026.09.27.5", "author": "mhale",
                 "deployed_at": _iso(now - timedelta(hours=19))},
            ],
        }


class ActionLog:
    """JSON Lines log of every write the ops platform accepted."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry = {"event": event, "recorded_at": _iso(datetime.now(timezone.utc)), **fields}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


def _minutes(window: str) -> int:
    try:
        return max(1, min(int(window.rstrip("m")), 60))
    except ValueError:
        return 15


def serve_in_background(log: ActionLog, world: World | None = None) -> tuple[str, Callable[[], None]]:
    """Start the ops platform on 127.0.0.1:<free port>. Returns (base_url, shutdown)."""

    world = world or World()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            query = parse_qs(url.query)
            if parts == ["alerts", ALERT_ID]:
                return self._send(200, world.alert())
            if parts == ["metrics", SERVICE, "error_rate"]:
                return self._send(200, world.error_rate(_minutes(query.get("window", ["15m"])[0])))
            if parts == ["deploys", SERVICE]:
                return self._send(200, world.deploys())
            return self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            parts = urlparse(self.path).path.strip("/").split("/")
            body = self._body()
            if len(parts) == 3 and parts[0] == "deploys" and parts[2] == "rollback":
                entry = log.record("deploy.rolled_back", deploy_id=parts[1], **body)
            elif parts == ["pages"]:
                entry = log.record("oncall.paged", **body)
            elif parts == ["statuspage", "incidents"]:
                entry = log.record("statuspage.posted", **body)
            elif len(parts) == 3 and parts[0] == "alerts" and parts[2] == "resolve":
                entry = log.record("alert.resolved", alert_id=parts[1], **body)
            else:
                return self._send(404, {"error": "not found"})
            return self._send(201, {"ok": True, **entry})

        def log_message(self, *_: Any) -> None:  # keep demo output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def shutdown() -> None:
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{server.server_address[1]}", shutdown
