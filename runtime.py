"""Model construction.

Default: the deterministic planner in ``mock_llm``, offline and free. Set
``INCIDENT_LIVE=1`` to use a real OpenAI-compatible endpoint through the
standard ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` variables, for example a
local Ollama:

    INCIDENT_LIVE=1 OPENAI_BASE_URL=http://127.0.0.1:11434/v1 \\
      OPENAI_API_KEY=ollama INCIDENT_MODEL=qwen3.5:27b python demo.py
"""

from __future__ import annotations

import os
from typing import Callable

from langchain_openai import ChatOpenAI

from mock_llm import serve_in_background

DEFAULT_MODEL = "gpt-4o-mini"


def _chat(**kwargs) -> ChatOpenAI:
    return ChatOpenAI(model=os.environ.get("INCIDENT_MODEL", DEFAULT_MODEL), temperature=0, **kwargs)


def open_model() -> tuple[ChatOpenAI, Callable[[], None]]:
    """The model the agent uses. Returns (model, teardown)."""

    if os.environ.get("INCIDENT_LIVE") == "1":
        model = _chat()  # OPENAI_BASE_URL and OPENAI_API_KEY come from the environment
        return model, model.root_client.close

    port, shutdown = serve_in_background()
    model = _chat(base_url=f"http://127.0.0.1:{port}/v1", api_key="offline")

    def teardown() -> None:
        model.root_client.close()
        shutdown()

    return model, teardown
