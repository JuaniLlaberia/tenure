from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from brain.deps import Settings

@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver]:
    """
    The LangGraph checkpointer: in memory without DATABASE_URL, otherwise Postgres on the same
    Supabase database (direct or session-mode connection; the transaction pooler breaks
    prepared statements). Creates the checkpoint tables on first use.
    """
    if settings.database_url is None:
        yield InMemorySaver()
        return
    url = settings.database_url.get_secret_value()
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        yield saver
