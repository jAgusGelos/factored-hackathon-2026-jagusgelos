"""App configuration.

`.env` is loaded here only for local development convenience. The deployed
image never ships `.env` (see .gitignore / AD-2 / AD-7) — in production, env
vars are set directly on the hosting platform.

AWS credentials are intentionally NOT read here: the running app has zero
AWS dependency by design (AD-2). Only `etl/` scripts touch AWS.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# AD-10: the LLM model name is a single config constant, never hardcoded per call site.
# Task 2.3b decision (2026-09-28): Haiku 4.5 default KEPT, not switched to Sonnet 5.
# ANTHROPIC_API_KEY is empty in this environment (no key was ever provisioned), so the
# manual quality checkpoint this task calls for (reviewing real entity-extraction/
# response-generation output against real transcripts) could not be run — there was no
# real LLM output to review. This is a genuine external dependency, not a code gap:
# app/llm.py, the 3 required conversation-case integration tests, and the PT toggle are
# all built and pass against a MOCKED Anthropic client (tests/test_conversation_flows.py,
# tests/test_portuguese_toggle.py). Default-to-Haiku is the lower-cost, lower-latency
# choice AD-10 already argues for, and there is no observed signal to justify Sonnet.
# ACTION NEEDED before Milestone 6: supply a real ANTHROPIC_API_KEY, re-run this
# checkpoint manually, and update this comment + AD-10 in plan.md with the outcome.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

LLM_TIMEOUT_SECONDS = 15.0
LLM_MAX_TOKENS = 512
LLM_RETRY_BACKOFF_SECONDS = (1.0, 2.0)
# Model budget of one /api/chat turn: every call_llm() of the request shares it
# (app/llm.py::turn_deadline), so the server answers before the browser gives
# up at 25 s. An attempt is not started with less than LLM_MIN_ATTEMPT_SECONDS left.
TURN_DEADLINE_SECONDS = 20.0
LLM_MIN_ATTEMPT_SECONDS = 1.0
# The explanation assessment is a short JSON object; a tight cap keeps it fast.
ASSESSMENT_MAX_TOKENS = 300

# The login screen offers an "Autocompletar" button for the demo account (AD-4 is a
# SIMULATED identity service over synthetic data). Set to 0 to hide the
# credentials, e.g. for anything that is not a labeled demo deployment.
SHOW_DEMO_CREDENTIALS = os.environ.get("SHOW_DEMO_CREDENTIALS", "1") == "1"

MAX_USERNAME_LENGTH = 128

# The dataset is a snapshot that ends 2026-06-17. Relative dates a customer
# types ("ayer", "la semana pasada") are resolved against this date, not the
# wall clock, so they land inside the data instead of months after it.
DATA_AS_OF = os.environ.get("DATA_AS_OF", "2026-06-18")

SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "4"))

APP_DB_PATH = REPO_ROOT / "data" / "app.db"
FIXTURE_DB_PATH = REPO_ROOT / "data" / "fixture.duckdb"
DEMO_USERS_PATH = REPO_ROOT / "data" / "demo_users.json"

STATIC_DIR = REPO_ROOT / "static"
