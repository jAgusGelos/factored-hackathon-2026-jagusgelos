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
# Default per AD-10; may be switched to "claude-sonnet-5" at the Milestone-2 checkpoint
# (Task 2.3b) if manual quality testing shows Haiku is insufficient for generation.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

LLM_TIMEOUT_SECONDS = 15.0
LLM_MAX_RETRIES = 2
LLM_RETRY_BACKOFF_SECONDS = (1.0, 2.0)

SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "4"))

APP_DB_PATH = REPO_ROOT / "data" / "app.db"
FIXTURE_DB_PATH = REPO_ROOT / "data" / "fixture.duckdb"
DEMO_USERS_PATH = REPO_ROOT / "data" / "demo_users.json"

STATIC_DIR = REPO_ROOT / "static"
