from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from brain.context import MAX_FETCH_CHARS, lessons_section, profile_section
from brain.deps import Deps
from brain.helpers.llm import ToolDef

SEARCH_RESULTS = 5

@dataclass
class ToolContext:
    deps: Deps
    business_id: str
    team_id: str
    task_type: str

@dataclass(frozen=True)
class BrainTool:
    """
    A read-only tool a specialist may call. `status` turns (persona name, arguments) into the
    Progress line shown to the founder.
    """

    name: str
    description: str
    parameters: dict
    run: Callable[[ToolContext, dict], Awaitable[str]]
    status: Callable[[str, dict], str]

async def _read_memory(ctx: ToolContext, args: dict) -> str:
    profile = await ctx.deps.store.get_profile(ctx.business_id)
    lessons = await ctx.deps.store.list_lessons(ctx.business_id, ctx.team_id, ctx.task_type)
    sections = [profile_section(profile), lessons_section(lessons)]
    return "\n\n".join(s for s in sections if s) or "Nothing is known about this business yet."

async def _web_search(ctx: ToolContext, args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Tool error: give a search query."
    results = await ctx.deps.tools.web_search(query, k=SEARCH_RESULTS)
    if not results:
        return f"No results for {query!r}."
    return "\n".join(
        f"{n}. {r.title}\n   {r.url}\n   {r.snippet}" for n, r in enumerate(results, start=1)
    )

async def _fetch_page(ctx: ToolContext, args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "Tool error: give a url."
    page = await ctx.deps.tools.fetch_page(url)
    if page is None:
        return "Couldn't open that page."
    text = page.text if len(page.text) <= MAX_FETCH_CHARS else page.text[:MAX_FETCH_CHARS] + "…"
    return f"{page.title or page.url}\n\n{text}"

def _string_param(name: str, description: str) -> dict:
    return {
        "type": "object",
        "properties": {name: {"type": "string", "description": description}},
        "required": [name],
    }

BRAIN_TOOLS: dict[str, BrainTool] = {
    "read_memory": BrainTool(
        name="read_memory",
        description="Read what the team knows about this business: profile and lessons.",
        parameters={"type": "object", "properties": {}},
        run=_read_memory,
        status=lambda name, args: f"{name} is checking what the team knows",
    ),
    "web_search": BrainTool(
        name="web_search",
        description="Search the web. Returns titles, urls and snippets.",
        parameters=_string_param("query", "What to search for"),
        run=_web_search,
        status=lambda name, args: f"{name} is searching: {args.get('query', '')}",
    ),
    "fetch_page": BrainTool(
        name="fetch_page",
        description="Open a web page and read its text.",
        parameters=_string_param("url", "The page to open"),
        run=_fetch_page,
        status=lambda name, args: f"{name} is reading {args.get('url', 'a page')}",
    ),
}

def tool_defs(names: list[str]) -> list[ToolDef]:
    return [
        ToolDef(
            name=BRAIN_TOOLS[name].name,
            description=BRAIN_TOOLS[name].description,
            parameters=BRAIN_TOOLS[name].parameters,
        )
        for name in names
        if name in BRAIN_TOOLS
    ]
