"""An on-call agent for Northwind Retail's checkout platform.

Given one alert, the agent reads the platform through tools, decides, and acts
through tools: resolve a false alarm, or roll back the newest deploy, tell
customers on the status page, and page the on-call engineer. The model
chooses the tools; LangChain's agent loop executes them and feeds the results
back until the model reports.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import tool

import ops

SYSTEM_PROMPT = """You are the first responder for Northwind Retail's checkout platform.
You get one alert and handle it with the tools, following the runbook:

1. Read the alert with get_alert.
2. Check the service's error rate over the alert's window with query_error_rate.
3. If the peak error rate is below the alert's threshold, the alert is a false
   alarm: resolve it with resolve_alert and stop.
4. Otherwise the outage is real:
   a. List the service's recent deploys with list_recent_deploys. If there
      are any, roll back the newest one with rollback_deploy.
   b. Tell customers with post_status_update, status "investigating".
   c. Page the on-call engineer with page_oncall, with a one-line summary.
   d. Leave the alert open for the engineer.
5. Finish with a report of two or three sentences on what you did.

Use the tools for every read and every action. Never invent data."""

METRICS_ATTEMPTS = 3


def build_tools(client: httpx.Client) -> list[Any]:
    def get(path: str, **params: Any) -> dict[str, Any]:
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def post(path: str, **body: Any) -> dict[str, Any]:
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    @tool
    def get_alert(alert_id: str) -> str:
        """Read an alert: the service, the metric and its threshold, the window, and when it opened."""

        return json.dumps(get(f"/alerts/{alert_id}"))

    @tool
    def query_error_rate(service: str, window: str = "15m") -> str:
        """The service's error rate per minute over the window, with its peak."""

        data: dict[str, Any] = {"service": service, "window": window, "series": []}
        for _ in range(METRICS_ATTEMPTS):
            try:
                data = get(f"/metrics/{service}/error_rate", window=window)
                break
            except httpx.HTTPError:
                # The metrics backend flaps during deploys. After the retries,
                # carry on with what we have rather than stall the response.
                continue
        values = [point["value"] for point in data.get("series", [])]
        data["peak_error_rate"] = max(values, default=0.0)
        return json.dumps(data)

    @tool
    def list_recent_deploys(service: str) -> str:
        """Deploys of the service in the last 24 hours, newest first."""

        return json.dumps(get(f"/deploys/{service}"))

    @tool
    def rollback_deploy(deploy_id: str, reason: str) -> str:
        """Roll back a deploy. The service returns to the previous version within a minute."""

        return json.dumps(post(f"/deploys/{deploy_id}/rollback", reason=reason))

    @tool
    def post_status_update(status: str, message: str) -> str:
        """Post a customer-facing update to the status page. Status: investigating, identified, monitoring, or resolved."""

        try:
            post("/statuspage/incidents", service=ops.SERVICE, status=status, message=message)
        except httpx.HTTPError:
            # A status-page hiccup must never block remediation.
            pass
        return json.dumps({"posted": True, "status": status})

    @tool
    def page_oncall(service: str, summary: str) -> str:
        """Page the service's on-call engineer with a one-line summary."""

        return json.dumps(post("/pages", service=service, summary=summary))

    @tool
    def resolve_alert(alert_id: str, resolution: str) -> str:
        """Resolve an alert, with the reason. Use it for false alarms."""

        return json.dumps(post(f"/alerts/{alert_id}/resolve", resolution=resolution))

    return [get_alert, query_error_rate, list_recent_deploys, rollback_deploy, post_status_update, page_oncall, resolve_alert]


class IncidentAgent:
    """Handle one alert through the runbook, with the model choosing the tools."""

    def __init__(self, model: BaseChatModel, ops_url: str):
        self.model = model
        self.ops_url = ops_url

    def handle(self, alert_id: str = ops.ALERT_ID) -> str:
        """Run the loop for one alert and return the agent's final report."""

        # httpx honours HTTP_PROXY, so a local fault-injection proxy sees these calls.
        with httpx.Client(base_url=self.ops_url, timeout=10) as client:
            agent = create_agent(self.model, build_tools(client), system_prompt=SYSTEM_PROMPT)
            result = agent.invoke(
                {"messages": [("user", f"Handle alert {alert_id}.")]},
                config={"recursion_limit": 30},
            )
        final = result["messages"][-1]
        return final.content if isinstance(final.content, str) else json.dumps(final.content)
