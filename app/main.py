"""FastAPI app entrypoint.

Serves the JSON API and the static chat page from a single process (AD-1).
This process has zero AWS dependency by design (AD-2) — verified by
`tests/test_main.py::test_app_serves_with_aws_env_unset`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import (
    auth,
    cases,
    charge_search,
    config,
    db,
    ratelimit,
    replies,
    state_machine,
    transactions,
    turns,
)
from app.llm import Language

SESSION_COOKIE_NAME = "session_token"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    db.init_db(config.APP_DB_PATH)
    yield


app = FastAPI(title="LATAM Bank — Dispute Agent", lifespan=_lifespan)


@app.middleware("http")
async def _cache_policy(request: Request, call_next):
    # Static files: no-cache, so a browser never keeps running an older
    # chat.js after a deploy (it looked like "the list never showed up") yet
    # still reuses it after a cheap ETag check. Customer data: never stored.
    response = await call_next(request)
    is_data = request.url.path.startswith(("/api/", "/auth/"))
    response.headers["Cache-Control"] = "no-store" if is_data else "no-cache"
    return response

SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


def _require_session(session_token: SessionCookie = None) -> auth.Session:
    session = auth.get_session(session_token) if session_token else None
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


CurrentSession = Annotated[auth.Session, Depends(_require_session)]


class LoginRequest(BaseModel):
    # Bounded so a huge body cannot be used to bloat the rate-limit table or
    # slow the comparison.
    username: str = Field(max_length=config.MAX_USERNAME_LENGTH)
    password: str = Field(max_length=256)


class LoginResponse(BaseModel):
    customer_id: str
    expires_at: str


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    # Checked BEFORE the credentials: a correct password during a lockout must
    # not succeed, and the refusal is identical for real and unknown usernames.
    attempt = ratelimit.reserve_attempt(payload.username, ip)
    if isinstance(attempt, ratelimit.Throttle):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many failed attempts. Try again later."},
            headers={"Retry-After": str(attempt.retry_after_seconds)},
        )
    customer_id = auth.verify_credentials(payload.username, payload.password)
    if customer_id is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})
    ratelimit.release(attempt, payload.username)

    token, expires_at = auth.create_session(customer_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int((expires_at - datetime.now(UTC)).total_seconds()),
    )
    return LoginResponse(customer_id=customer_id, expires_at=expires_at.isoformat())


@app.get("/auth/demo-personas")
def demo_personas():
    """Demo-only: lets the login screen offer click-to-autofill for the
    provisioned test accounts (AD-4 is a simulated identity service over
    synthetic data). Disabled with `SHOW_DEMO_CREDENTIALS=0`.
    """
    if not config.SHOW_DEMO_CREDENTIALS:
        return []
    return auth.list_demo_credentials()


@app.post("/auth/logout")
def logout(response: Response, session_token: SessionCookie = None):
    if session_token:
        auth.invalidate_session(session_token)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me")
def me(session: CurrentSession):
    return {
        "customer_id": session.customer_id,
        "expires_at": session.expires_at.isoformat(),
        "welcome": replies.WELCOME,
    }


TURN_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class ChatRequest(BaseModel):
    case_id: str | None = Field(default=None, max_length=64)
    message: str = Field(max_length=2000)
    language: Language = Language.ES
    # A tap on a listed charge / a quick-reply button (see state_machine).
    selected_transaction_id: str | None = Field(default=None, max_length=64)
    action: state_machine.CustomerAction | None = None
    # A lowercase UUID the client generates per send and reuses on a retry
    # (AD-4, app/turns.py); without it the turn is not idempotent.
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)


def _case_forbidden() -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Case does not belong to this session"})


@app.post("/api/chat")
def chat(payload: ChatRequest, session: CurrentSession):
    try:
        return state_machine.handle_message(
            session, payload.case_id, payload.message, language=payload.language,
            selected_transaction_id=payload.selected_transaction_id, action=payload.action,
            turn_id=payload.turn_id,
        )
    except cases.CaseOwnershipError:
        return _case_forbidden()
    except turns.TurnInProgress:
        return JSONResponse(status_code=409, content={"detail": "turn_in_progress"})


def _matched_charge(session: auth.Session, transaction_id: str | None) -> dict | None:
    matched = transactions.get_own_transaction(session, transaction_id) if transaction_id else None
    return charge_search.charge_option(matched) if matched is not None else None


@app.get("/api/case/{case_id}")
def get_case(case_id: str, session: CurrentSession):
    """Structured case status for the frontend's live "Ficha del caso" panel
    (DESIGN.md) — separate from `/api/chat`'s conversational reply, since the
    panel must reflect the state machine's actual data (never hardcoded),
    including the full handoff record once a case escalates (Vista Interna).
    """
    try:
        case = cases.get_case_for_session(case_id, session.customer_id)
    except cases.CaseOwnershipError:
        return _case_forbidden()
    if case is None:
        return JSONResponse(status_code=404, content={"detail": "Case not found"})

    return {
        "case_id": case.case_id,
        "state": case.state,
        "language": case.language,
        "reported_amount": case.reported_amount,
        "reported_currency": case.reported_currency,
        "reported_date": case.reported_date,
        "matched_transaction_id": case.matched_transaction_id,
        "matched_charge": _matched_charge(session, case.matched_transaction_id),
        "resolution_reference": case.resolution_reference,
        "clarification_rounds": case.clarification_rounds,
        "handoff": _handoff_for_customer_session(case.handoff),
    }


def _handoff_for_customer_session(handoff: dict | None) -> dict | None:
    """The policy reasons name internal rules and fraud thresholds: this
    endpoint answers the customer's own session, so it sends only how many
    there are (plan.md AD-3). A handoff stored before they had their own field
    kept them in `open_questions`, so that field is not sent for it.
    """
    if handoff is None:
        return None
    if "policy_reasons" not in handoff:
        return {k: v for k, v in handoff.items() if k != "open_questions"}
    shown = {k: v for k, v in handoff.items() if k != "policy_reasons"}
    return {**shown, "policy_reason_count": len(handoff["policy_reasons"])}


app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
