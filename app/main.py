"""FastAPI app entrypoint.

Serves the JSON API and the static chat page from a single process (AD-1).
This process has zero AWS dependency by design (AD-2) — verified by
`tests/test_main.py::test_app_serves_with_aws_env_unset`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import auth, cases, config, db, state_machine
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
    username: str
    password: str


class LoginResponse(BaseModel):
    customer_id: str
    expires_at: str


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response):
    customer_id = auth.verify_credentials(payload.username, payload.password)
    if customer_id is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})

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
    message: str
    language: Language = Language.ES


@app.post("/api/chat")
def chat(payload: ChatRequest, session: CurrentSession):
    try:
        return state_machine.handle_message(
            session, payload.case_id, payload.message, language=payload.language
        )
    except cases.CaseOwnershipError:
        return JSONResponse(status_code=403, content={"detail": "Case does not belong to this session"})


app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
