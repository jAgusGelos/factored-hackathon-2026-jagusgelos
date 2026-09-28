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

from app import auth, cases, config, db, ratelimit, state_machine
from app.llm import Language

SESSION_COOKIE_NAME = "session_token"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    db.init_db(config.APP_DB_PATH)
    yield


app = FastAPI(title="LATAM Bank — Dispute Agent", lifespan=_lifespan)

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
    return {"customer_id": session.customer_id, "expires_at": session.expires_at.isoformat()}


class ChatRequest(BaseModel):
    case_id: str | None = None
    message: str = Field(max_length=2000)
    language: Language = Language.ES


def _case_forbidden() -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Case does not belong to this session"})


@app.post("/api/chat")
def chat(payload: ChatRequest, session: CurrentSession):
    try:
        return state_machine.handle_message(
            session, payload.case_id, payload.message, language=payload.language
        )
    except cases.CaseOwnershipError:
        return _case_forbidden()


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
        "resolution_reference": case.resolution_reference,
        "clarification_rounds": case.clarification_rounds,
        "handoff": case.handoff,
    }


app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
