import pytest

from app import config


@pytest.fixture(autouse=True)
def _dummy_anthropic_api_key(monkeypatch):
    """No test should ever hit the real Anthropic API — every test that
    exercises `app.llm.call_llm` mocks `anthropic.Anthropic` itself. This
    just satisfies `call_llm`'s upfront "is a key configured" check, which
    exists to convert a real deployment's missing-key case into a graceful
    `LLMUnavailable` instead of an uncaught SDK `TypeError` (see
    tests/test_llm_fallback.py's dedicated missing-key test, which overrides
    this back to an empty string).
    """
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-dummy-key-not-real")


@pytest.fixture()
def real_fixture_app_db(tmp_path, monkeypatch):
    from app import db
    from tests.support import REAL_DEMO_USERS_PATH, REAL_FIXTURE_PATH

    db_path = tmp_path / "app.db"
    monkeypatch.setattr(config, "APP_DB_PATH", db_path)
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", REAL_FIXTURE_PATH)
    monkeypatch.setattr(config, "DEMO_USERS_PATH", REAL_DEMO_USERS_PATH)
    db.init_db(db_path)
    return db_path
