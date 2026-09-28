# incident-response-agent

A sample agent for trying [faultkit](https://faultkit.dev) on real code: a
Python LangChain agent with a real tool loop that ships without any faultkit
scenarios or resilience tests. Northwind Retail is fictional.

An on-call agent for Northwind Retail's checkout platform. It takes one alert,
checks the service's error rate, and either closes the alert as a false alarm
or starts the response: it rolls back the newest deploy, tells customers on the
status page, and pages the on-call engineer. A person takes it from there.

## How it works

- `agent.py`: a LangChain `create_agent` tool loop. The model reads the runbook
  in the system prompt and chooses the tools; the loop runs them and feeds the
  results back until the model reports.
- Seven tools over the ops platform's HTTP API: `get_alert`, `query_error_rate`,
  `list_recent_deploys`, `rollback_deploy`, `post_status_update`, `page_oncall`,
  and `resolve_alert`.
- `ops.py`: a local stand-in for the ops platform: alerts, metrics, deploys,
  paging, and the status page. Every action it accepts is appended to
  `.data/actions.jsonl`.
- `mock_llm.py`: an offline, deterministic model that follows the runbook, so
  the demo and the tests need no API key.

## Built to keep going

An incident is the worst time for the responder itself to stall:

- The metrics backend flaps during deploys, so error-rate queries retry, then
  carry on with what they have.
- A status-page hiccup never blocks remediation.
- Rollback and paging calls fail loudly, so a person hears about them.

## Run it

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python demo.py                  # a real outage
python demo.py --quiet-world    # the same alert when nothing is wrong
python -m pytest
```

Against a real model, for example a local Ollama:

```bash
INCIDENT_LIVE=1 OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_API_KEY=ollama \
  INCIDENT_MODEL=qwen3.5:27b python demo.py
```

## Try faultkit on it

1. Install the faultkit skill in Claude Code:

   ```text
   /plugin marketplace add faultkit/skills
   /plugin install faultkit@faultkit
   ```

2. In this repository, with the `.venv` from "Run it" active, run the skill:

   ```text
   /faultkit:values <what must never happen, in your own words>
   /faultkit:review
   /faultkit:prove-all
   /faultkit:harden
   /faultkit:prove-all
   ```

   `/faultkit:values` is optional; without it, the review infers the
   outcomes. The skill asks before it writes anything. When you say so, it
   keeps the proofs in `.faultkit/` and the gate tests in `tests/`. The first
   time it needs faultkit, it downloads a pinned, checksum-verified release.

3. Commit `.faultkit/` and the new tests, push a branch, and open a pull
   request. The workflow in `.github/workflows/faultkit.yml` runs the tests,
   then replays every proof with [faultkit/action](https://github.com/faultkit/action)
   and comments on the pull request with the proof table and the outcome
   coverage. Until `.faultkit/invariants/manifest.json` exists, it runs the
   tests only.
