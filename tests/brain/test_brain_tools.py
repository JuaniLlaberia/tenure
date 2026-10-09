from brain.common import new_id
from brain.context import MAX_FETCH_CHARS
from brain.templates.registries import TOOL_NAMES
from brain.tools import BRAIN_TOOLS, ToolContext, tool_defs
from contract import BusinessProfile, Lesson, PageContent, SearchResult

ACTION_NAMES = {"post_social", "send_email", "delete_social"}

def context(deps, team_id="t1"):
    return ToolContext(deps=deps, business_id="b1", team_id=team_id, task_type="social_post")

def test_tool_names_match_registry():
    assert set(BRAIN_TOOLS) == TOOL_NAMES

def test_no_action_tools_exist():
    assert not ACTION_NAMES & set(BRAIN_TOOLS)
    assert not ACTION_NAMES & {tool.name for tool in tool_defs(sorted(TOOL_NAMES))}

def test_tool_defs_follow_the_names_given():
    defs = tool_defs(["web_search", "read_memory"])
    assert [d.name for d in defs] == ["web_search", "read_memory"]
    assert all(d.description and d.parameters["type"] == "object" for d in defs)

async def test_web_search_formats_results(deps, tools):
    tools.search_results = [
        SearchResult(title="Launch tips", url="https://a.co/tips", snippet="Post early"),
        SearchResult(title="Café trends", url="https://b.co/cafes", snippet="Oat milk"),
    ]

    text = await BRAIN_TOOLS["web_search"].run(context(deps), {"query": "launch week"})

    assert ("web_search", {"query": "launch week", "k": 5}) in tools.calls
    for part in ["Launch tips", "https://a.co/tips", "Post early", "Café trends"]:
        assert part in text
    assert text.index("Launch tips") < text.index("Café trends")

async def test_web_search_without_results_says_so(deps):
    text = await BRAIN_TOOLS["web_search"].run(context(deps), {"query": "nothing"})
    assert text

async def test_fetch_page_truncates_and_handles_none(deps, tools):
    long_text = "a" * (MAX_FETCH_CHARS + 500)
    tools.pages = {"https://a.co": PageContent(url="https://a.co", title="A page", text=long_text)}

    text = await BRAIN_TOOLS["fetch_page"].run(context(deps), {"url": "https://a.co"})
    assert "A page" in text
    assert len(text) < MAX_FETCH_CHARS + 100

    missing = await BRAIN_TOOLS["fetch_page"].run(context(deps), {"url": "https://nope.co"})
    assert "couldn't" in missing.lower()

async def test_read_memory_reads_profile_and_lessons(deps):
    await deps.store.save_profile(
        BusinessProfile(
            business_id="b1", name="Juan's Studio", what_you_sell="Logos", customers="Cafés"
        )
    )
    await deps.store.save_lesson(
        Lesson(
            lesson_id=new_id(),
            business_id="b1",
            team_id="t1",
            kind="preference",
            text="No hashtags in posts",
            source="chat",
            created_at=deps.clock(),
        )
    )

    text = await BRAIN_TOOLS["read_memory"].run(context(deps), {})

    assert "Juan's Studio" in text
    assert "No hashtags in posts" in text
