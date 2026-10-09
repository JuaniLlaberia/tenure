from datetime import timedelta

import pytest
from telegram.error import RetryAfter, TimedOut

from app.chat import bot

@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", instant)

def failing(errors: list[Exception]):
    calls = []

    async def call():
        calls.append(1)
        if errors:
            raise errors.pop(0)
        return "ok"

    return call, calls

async def test_flood_control_is_waited_out():
    call, calls = failing([RetryAfter(timedelta(seconds=9))])
    assert await bot._retry(call, repeatable=False) == "ok"
    assert len(calls) == 2

async def test_timeouts_retry_only_when_repeatable():
    call, calls = failing([TimedOut()])
    assert await bot._retry(call, repeatable=True) == "ok"
    call, calls = failing([TimedOut()])
    with pytest.raises(TimedOut):
        await bot._retry(call, repeatable=False)
    assert len(calls) == 1

async def test_gives_up_after_three_attempts():
    call, calls = failing([RetryAfter(timedelta(seconds=1))] * 3)
    with pytest.raises(RetryAfter):
        await bot._retry(call, repeatable=True)
    assert len(calls) == 3
