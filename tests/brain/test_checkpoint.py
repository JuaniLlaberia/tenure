from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from brain.checkpoint import open_checkpointer
from brain.common import new_id
from brain.deps import Settings

class Counter(TypedDict):
    n: int

def counter_graph(saver):
    graph = StateGraph(Counter)
    graph.add_node("bump", lambda state: {"n": state["n"] + 1})
    graph.add_edge(START, "bump")
    graph.add_edge("bump", END)
    return graph.compile(checkpointer=saver)

async def test_in_memory_without_database_url():
    async with open_checkpointer(Settings()) as saver:
        assert isinstance(saver, InMemorySaver)

@pytest.mark.live
async def test_postgres_with_database_url():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    settings = Settings.from_env()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL is not set")
    config = {"configurable": {"thread_id": f"tenure-smoke-{new_id()}"}}

    async with open_checkpointer(settings) as saver:
        assert isinstance(saver, AsyncPostgresSaver)
        await counter_graph(saver).ainvoke({"n": 1}, config)

    async with open_checkpointer(settings) as saver:
        state = await counter_graph(saver).aget_state(config)
        assert state.values == {"n": 2}
