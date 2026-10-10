"""
Runs due schedules: every minute it finds schedules whose next run has come, starts them
through Flows (the team's queue), and moves next_run_at to the next occurrence. The same tick
lets Flows end forgotten prompts and send drafts held for their send time.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from contract import Cadence, Schedule

if TYPE_CHECKING:
    from app.chat.flows import Flows
    from app.store.base import AppStore

logger = logging.getLogger(__name__)

TICK = 60.0
GIVE_UP_AFTER = timedelta(hours=6)

def next_run(cadence: Cadence, after: datetime) -> datetime:
    """
    The first occurrence strictly after `after`, in UTC, counted in the cadence's time zone.
    """
    zone = ZoneInfo(cadence.timezone)
    local = after.astimezone(zone)
    candidate = local.replace(hour=cadence.hour, minute=cadence.minute, second=0, microsecond=0)
    if cadence.every == "month":
        candidate = candidate.replace(day=cadence.day or 1)
        while candidate <= local:
            year, month = divmod(candidate.month, 12)
            candidate = candidate.replace(year=candidate.year + year, month=month + 1)
        return candidate.astimezone(UTC)
    step = timedelta(days=1)
    while candidate <= local or (
        cadence.every == "week"
        and cadence.weekday is not None
        and candidate.weekday() != cadence.weekday
    ):
        candidate = (candidate + step).replace(hour=cadence.hour, minute=cadence.minute)
    return candidate.astimezone(UTC)

def _utcnow() -> datetime:
    return datetime.now(UTC)

class Scheduler:
    def __init__(
        self,
        store: "AppStore",
        flows: "Flows",
        clock: Callable[[], datetime] = _utcnow,
        tick: float = TICK,
    ) -> None:
        self._store = store
        self._flows = flows
        self._clock = clock
        self._tick = tick

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_due()
            except Exception:
                logger.exception("Checking schedules failed")
            try:
                await self._flows.tick()
            except Exception:
                logger.exception("Checking prompts and send times failed")
            await asyncio.sleep(self._tick)

    async def run_due(self) -> None:
        """
        Schedules them if new; starts the due ones whose team is free. A team that is waiting
        for an answer keeps its run waiting, for up to 6 hours, then skips to the next one.
        A missed run (the app was down) runs once.
        """
        now = self._clock()
        for schedule in await self._store.due_schedules(now):
            if schedule.next_run_at is None:
                await self._store.save_schedule(
                    schedule.model_copy(update={"next_run_at": next_run(schedule.cadence, now)})
                )
                continue
            if self._flows.team_busy(schedule.team_id):
                if now - schedule.next_run_at > GIVE_UP_AFTER:
                    logger.info("Skipping a run of %s: the team was busy", schedule.title)
                    await self._advance(schedule, now, ran=False)
                continue
            await self._advance(schedule, now, ran=True)
            self._flows.start_schedule(schedule)

    async def _advance(self, schedule: Schedule, now: datetime, ran: bool) -> None:
        update = {"next_run_at": next_run(schedule.cadence, now)}
        if ran:
            update["last_run_at"] = now
        await self._store.save_schedule(schedule.model_copy(update=update))
