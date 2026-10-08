"""
The founder's dashboard: one HTML page plus a small JSON API, served next to the bot.
Each business has a private link (/b/<token>); there is no login.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.chat.flows import Flows, Refused
from app.store.base import AppStore
from app.web.overview import build_overview
from contract import AutonomyLevel, Brain

PAGE = Path(__file__).with_name("dashboard.html")
LEVELS = list(AutonomyLevel)

class Hire(BaseModel):
    template: str

class Reject(BaseModel):
    reason: str | None = None

class Lower(BaseModel):
    team_id: str
    task_type: str

def _utcnow() -> datetime:
    return datetime.now(UTC)

def create_api(
    flows: Flows,
    store: AppStore,
    brain: Brain,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    api = FastAPI(title="Tenure dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    page = PAGE.read_text()

    @api.middleware("http")
    async def private(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    @api.exception_handler(Refused)
    async def refused(request: Request, error: Refused) -> JSONResponse:
        return JSONResponse({"message": str(error)}, status_code=409)

    async def business(token: str) -> str:
        business_id = await store.business_for_token(token)
        if business_id is None:
            message = "This dashboard link isn't valid. Send /dashboard for a new one."
            raise HTTPException(404, message)
        return business_id

    @api.get("/b/{token}", response_class=HTMLResponse)
    async def dashboard(token: str) -> str:
        await business(token)
        return page

    @api.get("/api/b/{token}/overview")
    async def overview(token: str) -> dict[str, Any]:
        return await build_overview(store, brain, await business(token), clock())

    @api.post("/api/b/{token}/hire")
    async def hire(token: str, body: Hire) -> dict[str, str]:
        await flows.hire_from_dashboard(await business(token), body.template)
        return {"message": "Hiring now. The team's topic will appear in Telegram."}

    @api.post("/api/b/{token}/approvals/{approval_id}/approve")
    async def approve(token: str, approval_id: str) -> dict[str, str]:
        await flows.decide_from_dashboard(await business(token), approval_id, approve=True)
        return {"message": "Approved. The result shows up in Telegram."}

    @api.post("/api/b/{token}/approvals/{approval_id}/reject")
    async def reject(token: str, approval_id: str, body: Reject) -> dict[str, str]:
        business_id = await business(token)
        await flows.decide_from_dashboard(
            business_id, approval_id, approve=False, reason=body.reason
        )
        if body.reason and body.reason.strip():
            return {"message": "Rejected. The team will revise it and learn from your reason."}
        return {"message": "Rejected and dropped."}

    @api.post("/api/b/{token}/trust/lower")
    async def lower(token: str, body: Lower) -> dict[str, str]:
        business_id = await business(token)
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

    @api.post("/api/b/{token}/lessons/{lesson_id}/forget")
    async def forget(token: str, lesson_id: str) -> dict[str, str]:
        await flows.forget_lesson(await business(token), lesson_id)
        return {"message": "Forgotten. The team won't use this anymore."}

    return api
