"""
The founder's dashboard: one HTML page plus a small JSON API, served next to the bot.
Each business has a random link (/b/<token>) and a password sent with /dashboard in Telegram.
Everything under /b/<token>/api except login needs the session cookie for that link.
"""

import asyncio
import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.chat.flows import Flows, Refused
from app.store.base import AppStore
from app.web import auth
from app.web.overview import PROMOTE_AFTER_MAX, build_overview
from contract import AutonomyLevel, Brain, PlannedAction, PostSocial, SendEmail

PAGE = Path(__file__).with_name("dashboard.html")
COOKIE = "tenure_session"
LEVELS = list(AutonomyLevel)
GONE = "This dashboard link isn't valid or was turned off. Send /dashboard for a new one."
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
POST_LIMIT = 300
LOCKED = "Too many wrong tries. Wait 5 minutes, or send /dashboard for a new password."

class Login(BaseModel):
    password: str

class Hire(BaseModel):
    template: str

class Approve(BaseModel):
    text: str | None = None
    subject: str | None = None
    to: str | None = None

class Reject(BaseModel):
    reason: str | None = None

class Lower(BaseModel):
    team_id: str
    task_type: str

class Threshold(BaseModel):
    team_id: str
    promote_after: int = Field(ge=1, le=PROMOTE_AFTER_MAX)

def _edited(planned: PlannedAction | None, body: Approve) -> PlannedAction | None:
    """
    The founder's version of the action, or None if nothing changed.
    """
    if isinstance(planned, PostSocial):
        text = planned.text if body.text is None else body.text
        if text == planned.text:
            return None
        if not text.strip():
            raise Refused("The post can't be empty.")
        if len(text) > POST_LIMIT:
            raise Refused(f"That's {len(text)} characters; Bluesky allows {POST_LIMIT}.")
        return PostSocial(text=text)
    if isinstance(planned, SendEmail):
        version = SendEmail(
            to=(planned.to if body.to is None else body.to).strip(),
            subject=(planned.subject if body.subject is None else body.subject).strip(),
            body=planned.body if body.text is None else body.text,
        )
        if version == planned:
            return None
        if not EMAIL.fullmatch(version.to):
            raise Refused("That email address doesn't look right.")
        if not version.subject or not version.body.strip():
            raise Refused("The email needs a subject and a body.")
        return version
    return None

def _utcnow() -> datetime:
    return datetime.now(UTC)

def _https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded == "https"

def create_api(
    flows: Flows,
    store: AppStore,
    brain: Brain,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    api = FastAPI(title="Tenure dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    page = PAGE.read_text()
    attempts = auth.Attempts()

    @api.middleware("http")
    async def private(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @api.exception_handler(Refused)
    async def refused(request: Request, error: Refused) -> JSONResponse:
        return JSONResponse({"message": str(error)}, status_code=409)

    async def access(token: str) -> tuple[str, str]:
        found = await store.dashboard_access(token)
        if found is None:
            raise HTTPException(404, GONE)
        return found

    async def business(request: Request, token: str) -> str:
        business_id, password_hash = await access(token)
        cookie = request.cookies.get(COOKIE, "")
        if not hmac.compare_digest(cookie, auth.session_value(password_hash, token)):
            raise HTTPException(401, "Sign in with the password from Telegram.")
        return business_id

    @api.get("/b/{token}", response_class=HTMLResponse)
    async def dashboard(token: str) -> str:
        await access(token)
        return page

    @api.post("/b/{token}/api/login")
    async def login(token: str, body: Login, request: Request, response: Response) -> dict:
        _, password_hash = await access(token)
        key = f"{token}:{password_hash}"
        now = clock()
        if attempts.locked(key, now):
            raise HTTPException(429, LOCKED)
        if not await asyncio.to_thread(auth.verify_password, body.password, password_hash):
            left = attempts.fail(key, now)
            if left == 0:
                raise HTTPException(429, LOCKED)
            tries = "try" if left == 1 else "tries"
            raise HTTPException(401, f"That password didn't work. {left} {tries} left.")
        attempts.clear(key)
        response.set_cookie(
            COOKIE,
            auth.session_value(password_hash, token),
            max_age=int(timedelta(days=auth.SESSION_DAYS).total_seconds()),
            path=f"/b/{token}",
            httponly=True,
            samesite="strict",
            secure=_https(request),
        )
        return {"message": "Signed in."}

    @api.post("/b/{token}/api/logout")
    async def logout(token: str, response: Response) -> dict:
        response.delete_cookie(COOKIE, path=f"/b/{token}")
        return {"message": "Signed out on this browser."}

    @api.get("/b/{token}/api/overview")
    async def overview(token: str, request: Request) -> dict[str, Any]:
        business_id = await business(request, token)
        return await build_overview(store, brain, business_id, clock(), flows.deciding)

    @api.post("/b/{token}/api/hire")
    async def hire(token: str, body: Hire, request: Request) -> dict[str, str]:
        await flows.hire_from_dashboard(await business(request, token), body.template)
        return {"message": "Hiring now. The team's topic will appear in Telegram."}

    @api.post("/b/{token}/api/approvals/{approval_id}/approve")
    async def approve(
        token: str, approval_id: str, request: Request, body: Approve | None = None
    ) -> dict[str, str]:
        business_id = await business(request, token)
        approval = await store.get_approval(approval_id)
        if approval is None or approval.business_id != business_id:
            raise Refused("I can't find that draft.")
        version = _edited(approval.planned_action, body or Approve())
        if version is None:
            await flows.decide_from_dashboard(business_id, approval_id, "approve")
            return {"message": "Approved. The result shows up in Telegram too."}
        text = version.text if isinstance(version, PostSocial) else version.body
        whole = isinstance(version, SendEmail) and (
            version.to != approval.planned_action.to
            or version.subject != approval.planned_action.subject
        )
        await flows.decide_from_dashboard(
            business_id,
            approval_id,
            "edit",
            edited_text=text,
            edited_action=version if whole else None,
        )
        return {"message": "Your version is going out. The team will learn from your changes."}

    @api.post("/b/{token}/api/approvals/{approval_id}/reject")
    async def reject(token: str, approval_id: str, body: Reject, request: Request) -> dict:
        business_id = await business(request, token)
        await flows.decide_from_dashboard(business_id, approval_id, "reject", reason=body.reason)
        if body.reason and body.reason.strip():
            return {"message": "Rejected. The team will revise it and learn from your reason."}
        return {"message": "Rejected and dropped."}

    @api.post("/b/{token}/api/actions/{action_id}/undo")
    async def undo(token: str, action_id: str, request: Request) -> dict[str, str]:
        await flows.undo_from_dashboard(await business(request, token), action_id)
        return {"message": "Undoing. The post will be deleted from Bluesky."}

    @api.post("/b/{token}/api/trust/lower")
    async def lower(token: str, body: Lower, request: Request) -> dict[str, str]:
        business_id = await business(request, token)
        team = await store.get_team(body.team_id)
        trust = await store.get_trust(body.team_id, body.task_type)
        if team is None or team.business_id != business_id or trust is None:
            raise Refused("I can't find that team.")
        index = LEVELS.index(trust.level)
        if index == 0:
            raise Refused("This is already the lowest level.")
        update = {"level": LEVELS[index - 1], "approval_streak": 0, "updated_at": clock()}
        await store.set_trust(trust.model_copy(update=update))
        return {"message": "Lowered. The team will ask more often from now on."}

    @api.post("/b/{token}/api/trust/threshold")
    async def threshold(token: str, body: Threshold, request: Request) -> dict[str, str]:
        business_id = await business(request, token)
        team = await store.get_team(body.team_id)
        if team is None or team.business_id != business_id:
            raise Refused("I can't find that team.")
        for trust in await store.list_trust(body.team_id):
            update = {"promote_after": body.promote_after, "updated_at": clock()}
            await store.set_trust(trust.model_copy(update=update))
        n = body.promote_after
        approvals = "approval" if n == 1 else "approvals"
        return {"message": f"The team asks for more autonomy after {n} {approvals}."}

    @api.post("/b/{token}/api/lessons/{lesson_id}/forget")
    async def forget(token: str, lesson_id: str, request: Request) -> dict[str, str]:
        await flows.forget_lesson(await business(request, token), lesson_id)
        return {"message": "Forgotten. The team won't use this anymore."}

    return api
