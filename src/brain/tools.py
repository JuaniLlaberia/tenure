from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from brain.common import new_id
from brain.context import MAX_FETCH_CHARS, fence, lessons_section, profile_section
from brain.deps import Deps
from brain.helpers.llm import ToolDef
from contract import FileKind, FileRef

SEARCH_RESULTS = 5
MAX_IMAGE_CALLS = 2
ASPECT_RATIOS = ("1:1", "16:9")
STATUS_PREVIEW = 60
PLAN_PREFIX = "plan-"

def is_plan(file: FileRef) -> bool:
    """
    A planned image: its prompt is known, but it's only made after the lead's review.
    """
    return file.file_id.startswith(PLAN_PREFIX) and file.size_bytes == 0

@dataclass
class ToolContext:
    """
    What a tool may use during one specialist step. `files`, `prompts` and `aspects` collect
    the images the step planned; `image_calls` counts generate_image calls against
    `image_limit` (the step's limit, lower when the request is close to its image budget).
    """

    deps: Deps
    business_id: str
    team_id: str
    task_type: str
    files: list[FileRef] = field(default_factory=list)
    prompts: dict[str, str] = field(default_factory=dict)
    aspects: dict[str, str] = field(default_factory=dict)
    image_calls: int = 0
    image_limit: int = MAX_IMAGE_CALLS

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
    listed = "\n".join(
        f"{n}. {r.title}\n   {r.url}\n   {r.snippet}" for n, r in enumerate(results, start=1)
    )
    return fence(listed, "search results")

async def _fetch_page(ctx: ToolContext, args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "Tool error: give a url."
    page = await ctx.deps.tools.fetch_page(url)
    if page is None:
        return "Couldn't open that page."
    text = page.text if len(page.text) <= MAX_FETCH_CHARS else page.text[:MAX_FETCH_CHARS] + "…"
    return fence(f"{page.title or page.url}\n\n{text}", f"web page {page.url}")

async def _generate_image(ctx: ToolContext, args: dict) -> str:
    """
    Plans one image: the prompt, alt text and shape, under a placeholder id the model puts in
    its result. Nothing is generated here: the team graph makes planned images once, after
    the lead's review passed, so a revision never pays for an image that gets thrown away.
    """
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Tool error: give a prompt."
    alt_text = str(args.get("alt_text", "")).strip() or prompt[:120]
    aspect_ratio = str(args.get("aspect_ratio") or "1:1")
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = "1:1"
    if ctx.deps.images is None:
        return "Tool error: image generation isn't set up."
    if ctx.image_calls >= ctx.image_limit:
        return (
            "Tool error: no more images for this step; finish with the images you have, or "
            "with none."
        )
    ctx.image_calls += 1
    file = FileRef(
        file_id=f"{PLAN_PREFIX}{new_id()[:8]}",
        business_id=ctx.business_id,
        kind=FileKind.IMAGE,
        mime_type="image/png",
        size_bytes=0,
        source="generated",
        alt_text=alt_text,
        created_at=ctx.deps.clock(),
    )
    ctx.files.append(file)
    ctx.prompts[file.file_id] = prompt
    ctx.aspects[file.file_id] = aspect_ratio
    return f"Saved image {file.file_id}: {alt_text} (planned; made once the draft passes review)"

def _drawing(name: str, args: dict) -> str:
    subject = str(args.get("alt_text") or args.get("prompt") or "an image")
    if len(subject) > STATUS_PREVIEW:
        subject = subject[: STATUS_PREVIEW - 1] + "…"
    return f"{name} is drawing: {subject}"

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
    "generate_image": BrainTool(
        name="generate_image",
        description=(
            "Plan one image from a detailed prompt. It's made after the lead's review, so write "
            "the prompt you want made. Returns the image's id; put that id in your result's "
            "`images`."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Subject, composition, style, palette and light",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": list(ASPECT_RATIOS),
                    "description": "1:1 for posts, 16:9 for a newsletter header",
                },
                "alt_text": {
                    "type": "string",
                    "description": "One sentence saying what the image shows",
                },
            },
            "required": ["prompt", "alt_text"],
        },
        run=_generate_image,
        status=_drawing,
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
