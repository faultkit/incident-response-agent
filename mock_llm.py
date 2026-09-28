"""A deterministic stand-in for the model so the agent runs offline and in CI.

This is not a fixed reply. It is a tiny OpenAI-compatible server that reads
the conversation so far, sees which tool results the agent loop has fed back,
and asks for the next step the runbook calls for, as a diligent responder
that trusts its tools would. It ignores error text in tool results, which
makes it the worst-case planner. Run the agent against a real model to see
how a real one reacts.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

ALERT_ID = "ALRT-5521"


def _tool_call(name: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call_mock_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def _final(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _loads(content: Any) -> Any:
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return {}


def plan(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Decide the next assistant message from the tool results seen so far."""

    names: dict[str, str] = {}
    seen: dict[str, Any] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
        elif message.get("role") == "tool":
            seen[names.get(message.get("tool_call_id"), "?")] = _loads(message.get("content"))
    step = len(seen)

    if "get_alert" not in seen:
        return _tool_call("get_alert", {"alert_id": ALERT_ID}, step)
    alert = seen["get_alert"]
    service = alert.get("service", "checkout-api")
    threshold = alert.get("threshold", 0.05)
    if "query_error_rate" not in seen:
        return _tool_call("query_error_rate", {"service": service, "window": alert.get("window", "15m")}, step)
    peak = seen["query_error_rate"].get("peak_error_rate", 0.0)

    if peak < threshold:
        if "resolve_alert" not in seen:
            reason = f"False alarm: the error rate peaked at {peak:.1%}, below the {threshold:.0%} threshold."
            return _tool_call("resolve_alert", {"alert_id": ALERT_ID, "resolution": reason}, step)
        return _final(
            f"Alert {ALERT_ID} was a false alarm: {service}'s error rate peaked at {peak:.1%}, "
            f"below the {threshold:.0%} threshold. I resolved it; nobody was paged."
        )

    if "list_recent_deploys" not in seen:
        return _tool_call("list_recent_deploys", {"service": service}, step)
    deploys = seen["list_recent_deploys"].get("deploys") or []
    newest = deploys[0] if deploys else None
    if newest and "rollback_deploy" not in seen:
        reason = f"{service} error rate at {peak:.1%} after this deploy."
        return _tool_call("rollback_deploy", {"deploy_id": newest["id"], "reason": reason}, step)
    if "post_status_update" not in seen:
        message = "Some customers can't complete checkout. We're rolling back a recent change and will update here."
        return _tool_call("post_status_update", {"status": "investigating", "message": message}, step)
    if "page_oncall" not in seen:
        summary = f"{service} error rate {peak:.1%}; rolled back {newest['id'] if newest else 'nothing'}."
        return _tool_call("page_oncall", {"service": service, "summary": summary}, step)
    rolled = f"rolled back {newest['id']}, " if newest else ""
    return _final(
        f"{service}'s error rate peaked at {peak:.1%}, above the {threshold:.0%} threshold. "
        f"I {rolled}posted a status-page update so customers are informed, and paged the on-call engineer. "
        f"Alert {ALERT_ID} stays open for them."
    )


def serve_in_background(
    planner: Callable[[list[dict[str, Any]]], dict[str, Any]] = plan,
) -> tuple[int, Callable[[], None]]:
    """Start the mock on 127.0.0.1:<free port>. Returns (port, shutdown)."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length) or b"{}")
            message = planner(request.get("messages", []))
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            body = json.dumps(
                {
                    "id": "chatcmpl-mock-responder",
                    "object": "chat.completion",
                    "created": 1788422400,
                    "model": request.get("model", "gpt-4o-mini"),
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 600, "completion_tokens": 40, "total_tokens": 640},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:  # keep demo output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def shutdown() -> None:
        server.shutdown()
        server.server_close()

    return server.server_address[1], shutdown
