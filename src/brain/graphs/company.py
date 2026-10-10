import logging
import re
from typing import TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from brain.common import new_id
from brain.deps import Deps
from brain.helpers.decide import decide
from brain.prompts.chief_of_staff import (
    ANSWER_CLEAR,
    CHANGE_PHRASES,
    CHANGED_QUESTION,
    PLACEHOLDERS,
    PROFILE_UPDATED,
    SKIPPED,
    TIMEZONE_QUESTION,
    TIMEZONE_SET,
    TIMEZONE_SKIP,
    TOPICS,
    UPDATES_PROFILE,
    Extraction,
    ProfileDraft,
    TimezoneAnswer,
    extract_messages,
    fallback_question,
    question_messages,
    reply_messages,
    timezone_messages,
)
from brain.reasoning import needs_reasoning
from contract import (
    Ask,
    BusinessProfile,
    Event,
    Lesson,
    OnboardingComplete,
    PageContent,
    Persona,
    Say,
)

log = logging.getLogger(__name__)

CHIEF_OF_STAFF = Persona(name="Alex", role="Chief of staff", avatar="company/alex.png")
REQUIRED = ("name", "what_you_sell", "customers")
MAX_TURNS = 8
MAX_PAGES = 2
URL = re.compile(r"https?://[^\s<>\"')\]]+")
BARE_DOMAIN = re.compile(
    r"(?<![\w@./:-])((?:[a-z0-9-]+\.)+([a-z]{2,24}))(/[^\s<>\"')\]]*)?(?![\w@-])", re.IGNORECASE
)
TLDS = {
    "com", "net", "org", "io", "co", "ai", "app", "dev", "shop", "store", "biz", "info", "site",
    "online", "studio", "design", "agency", "tech", "page", "blog", "xyz",
}
NOT_TLDS = {"js", "ts", "py", "rb", "md", "sh", "db", "ps", "cs", "gz"}

def find_links(text: str) -> list[str]:
    """
    The links in an answer, in order: full URLs, then bare domains like "mysite.com" or
    "www.mysite.com/about" with https:// added. A bare domain needs a common or two-letter
    country ending, so emails and file names like "menu.pdf" aren't links.
    """
    links = [url.rstrip(".,;:!?") for url in URL.findall(text)]
    rest = URL.sub(" ", text)
    for match in BARE_DOMAIN.finditer(rest):
        tld = match.group(2).lower()
        if tld in NOT_TLDS or (tld not in TLDS and len(tld) != 2):
            continue
        links.append("https://" + match.group(0).rstrip(".,;:!?"))
    return list(dict.fromkeys(links))

FIRST_QUESTION = (
    f"Hi, I'm {CHIEF_OF_STAFF.name}, your chief of staff. I'll learn about your business once, "
    "so every team you hire already knows it. What's the business called, and what do you sell? "
    "If you have a website, paste the link and I'll read it."
)

class CompanyState(TypedDict, total=False):
    business_id: str
    restart: bool
    message: str | None
    onboarded: bool
    draft: dict
    facts: list[str]
    question: str | None
    answer: str | None
    files: list[dict]
    unclear: bool
    asked_tone: bool
    turns: int
    misses: int
    skipped: list[str]
    asked_timezone: bool

def missing_fields(
    draft: ProfileDraft, asked_tone: bool, skipped: list[str] | None = None
) -> list[str]:
    skipped = skipped or []
    missing = [field for field in REQUIRED if not getattr(draft, field) and field not in skipped]
    if not draft.tone and not asked_tone and "tone" not in skipped:
        missing.append("tone")
    return missing

def is_complete(
    draft: ProfileDraft, asked_tone: bool, turns: int, skipped: list[str] | None = None
) -> bool:
    """
    Plain code decides when onboarding is done: every required field given or skipped, and
    tone either given, asked once, or out of turns. Twice the turns ends it whatever is missing.
    """
    if turns >= 2 * MAX_TURNS:
        return True
    skipped = skipped or []
    if any(not getattr(draft, field) and field not in skipped for field in REQUIRED):
        return False
    return bool(draft.tone) or asked_tone or turns >= MAX_TURNS or "tone" in skipped

def changes_summary(before: ProfileDraft, after: ProfileDraft, city: str | None) -> str:
    """
    What a profile update changed, in the founder's words: "you sell X and Y. Times now use
    Buenos Aires time."
    """
    phrases = [
        CHANGE_PHRASES[field].format(value=getattr(after, field))
        for field in CHANGE_PHRASES
        if getattr(after, field) and getattr(after, field) != getattr(before, field)
    ]
    sentences = ["; ".join(phrases) + "."] if phrases else []
    if city and after.extra.get("timezone") != before.extra.get("timezone"):
        sentences.append(f"Times now use {city} time.")
    summary = " ".join(sentences)
    return summary[:1].lower() + summary[1:]

def with_placeholders(draft: ProfileDraft) -> ProfileDraft:
    """
    A required field the founder skipped gets a placeholder, so teams can still be hired.
    """
    update = {field: PLACEHOLDERS[field] for field in REQUIRED if not getattr(draft, field)}
    return draft.model_copy(update=update)

async def resolve_timezone(deps: Deps, text: str) -> tuple[str, str] | None:
    """
    The city and IANA timezone a message names, checked with zoneinfo; None when it names none.
    """
    try:
        result = await deps.llm.structured(
            deps.settings.model_lead, timezone_messages(text), TimezoneAnswer, reasoning=False
        )
    except Exception as error:
        log.warning("Couldn't read a timezone: %s", error)
        return None
    city, name = (result.value.city or "").strip(), (result.value.timezone or "").strip()
    if not city or not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return city, name

def merge(draft: ProfileDraft, found: ProfileDraft, links: list[str]) -> ProfileDraft:
    """
    Fields the answer gives fill or replace; lists grow without duplicates; extra merges.
    """
    update: dict = {}
    for field in ("name", "what_you_sell", "customers", "prices", "tone"):
        value = (getattr(found, field) or "").strip()
        if value:
            update[field] = value
    update["main_clients"] = list(dict.fromkeys([*draft.main_clients, *found.main_clients]))
    update["links"] = list(dict.fromkeys([*draft.links, *found.links, *links]))
    update["extra"] = {**draft.extra, **found.extra}
    return draft.model_copy(update=update)

def build_company_graph(deps: Deps, checkpointer) -> CompiledStateGraph:
    """
    Business onboarding in the General topic, then the chief of staff's replies.
    """

    def emit(event: Event) -> None:
        get_stream_writer()(event)

    def draft_of(state: CompanyState) -> ProfileDraft:
        return ProfileDraft.model_validate(state.get("draft") or {})

    async def entry(state: CompanyState) -> dict:
        profile = await deps.store.get_profile(state["business_id"])
        update: dict = {"onboarded": profile is not None}
        if state.get("restart"):
            update.update(
                draft={},
                facts=[],
                question=None,
                answer=None,
                unclear=False,
                asked_tone=False,
                turns=0,
                misses=0,
                skipped=[],
                asked_timezone=False,
            )
        return update

    async def question(state: CompanyState) -> dict:
        draft = draft_of(state)
        asked_tone = state.get("asked_tone", False)
        missing = missing_fields(draft, asked_tone, state.get("skipped"))
        if state.get("turns", 0) == 0 and not state.get("answer"):
            return {"question": FIRST_QUESTION}
        messages = question_messages(
            CHIEF_OF_STAFF,
            draft,
            missing,
            state.get("question"),
            state.get("answer"),
            state.get("unclear", False),
        )
        try:
            completion = await deps.llm.complete(
                deps.settings.model_lead, messages, reasoning=False
            )
            text = (completion.text or "").strip()
        except Exception as error:
            log.warning("Chief of staff question failed, using a fixed one: %s", error)
            text = ""
        return {
            "question": text or fallback_question(missing),
            "asked_tone": asked_tone or "tone" in missing,
        }

    async def wait(state: CompanyState) -> dict:
        ask = Ask(team_id=None, persona=CHIEF_OF_STAFF, question=state["question"])
        value = interrupt(ask.model_dump(mode="json"))
        if isinstance(value, dict):
            return {"answer": str(value.get("text", "")), "files": list(value.get("files", []))}
        return {"answer": str(value), "files": []}

    async def extract(state: CompanyState) -> dict:
        answer = state["answer"]
        links = find_links(answer)
        pages = []
        for url in links[:MAX_PAGES]:
            page = await deps.tools.fetch_page(url)
            if page is not None:
                pages.append(page)
        pages.extend(
            PageContent(url=f"file:{item['title']}", title=item["title"], text=item["text"])
            for item in state.get("files") or []
        )
        draft = draft_of(state)
        try:
            result = await deps.llm.structured(
                deps.settings.model_lead,
                extract_messages(state["question"], answer, pages, draft),
                Extraction,
                reasoning=False,
            )
            found = result.value
        except Exception as error:
            log.warning("Profile extraction failed, nothing learned from this answer: %s", error)
            found = Extraction(fields=ProfileDraft())
        merged = merge(draft, found.fields, links)
        learned = merged.model_dump(exclude={"links"}) != draft.model_dump(exclude={"links"})
        clarity = await decide(
            deps, {"answer_clear": ANSWER_CLEAR}, f"Question: {state['question']}\nAnswer: {answer}"
        )
        clear = clarity["answer_clear"].accepts("clear", deps.settings.decide_threshold)
        facts = list(dict.fromkeys([*state.get("facts", []), *found.facts]))
        unclear = not clear and not learned
        misses = state.get("misses", 0) + 1 if unclear else 0
        skipped = list(state.get("skipped") or [])
        missing = missing_fields(merged, state.get("asked_tone", False), skipped)
        if misses >= 2 and missing:
            skipped.append(missing[0])
            text = SKIPPED.format(topic=TOPICS[missing[0]])
            emit(Say(team_id=None, persona=CHIEF_OF_STAFF, text=text))
            misses, unclear = 0, False
        return {
            "draft": merged.model_dump(mode="json"),
            "facts": facts,
            "unclear": unclear,
            "turns": state.get("turns", 0) + 1,
            "misses": misses,
            "skipped": skipped,
        }

    def after_extract(state: CompanyState) -> str:
        draft = draft_of(state)
        done = is_complete(
            draft,
            state.get("asked_tone", False),
            state.get("turns", 0),
            state.get("skipped"),
        )
        if not done:
            return "question"
        if not state.get("asked_timezone") and "timezone" not in draft.extra:
            return "timezone"
        return "finish"

    async def timezone(state: CompanyState) -> dict:
        """
        The last interview question: whose clock posts and schedules use. A skip, or a place
        that isn't a real timezone, leaves it unset (Los Angeles).
        """
        ask = Ask(
            team_id=None,
            persona=CHIEF_OF_STAFF,
            question=TIMEZONE_QUESTION,
            quick_replies=[TIMEZONE_SKIP],
        )
        value = interrupt(ask.model_dump(mode="json"))
        answer = str(value.get("text", "")) if isinstance(value, dict) else str(value)
        update: dict = {"asked_timezone": True}
        if answer.strip() == TIMEZONE_SKIP:
            return update
        found = await resolve_timezone(deps, answer)
        if found is None:
            return update
        city, name = found
        draft = draft_of(state)
        draft = draft.model_copy(update={"extra": {**draft.extra, "timezone": name}})
        emit(Say(team_id=None, persona=CHIEF_OF_STAFF, text=TIMEZONE_SET.format(city=city)))
        return {**update, "draft": draft.model_dump(mode="json")}

    async def finish(state: CompanyState) -> dict:
        draft = with_placeholders(draft_of(state))
        profile = BusinessProfile(business_id=state["business_id"], **draft.model_dump())
        await deps.store.save_profile(profile)
        for fact in state.get("facts", []):
            await deps.store.save_lesson(
                Lesson(
                    lesson_id=new_id(),
                    business_id=state["business_id"],
                    team_id=None,
                    kind="fact",
                    text=fact,
                    source="business_onboarding",
                    created_at=deps.clock(),
                )
            )
        emit(OnboardingComplete(scope="business", team_id=None))
        hire = ", ".join(f"/hire {name}" for name in deps.templates)
        emit(
            Say(
                team_id=None,
                persona=CHIEF_OF_STAFF,
                text=(
                    f"Thanks, I have what I need about {profile.name}. Next, hire your first "
                    f"team: {hire}."
                ),
            )
        )
        return {"onboarded": True}

    async def update_profile(profile: BusinessProfile, message: str) -> str | None:
        """
        A message in General that changes facts about the business updates the profile
        (timezone included); returns what changed, or None.
        """
        decisions = await decide(
            deps, {"updates_profile": UPDATES_PROFILE}, f"The founder wrote:\n{message}"
        )
        if not decisions["updates_profile"].accepts("yes", deps.settings.decide_threshold):
            return None
        draft = ProfileDraft.model_validate(profile.model_dump(exclude={"business_id"}))
        try:
            result = await deps.llm.structured(
                deps.settings.model_lead,
                extract_messages(CHANGED_QUESTION, message, [], draft),
                Extraction,
                reasoning=False,
            )
            found = result.value
        except Exception as error:
            log.warning("Profile update extraction failed: %s", error)
            found = Extraction(fields=ProfileDraft())
        merged = merge(draft, found.fields, [])
        zone = await resolve_timezone(deps, message)
        if zone is not None:
            merged = merged.model_copy(update={"extra": {**merged.extra, "timezone": zone[1]}})
        summary = changes_summary(draft, merged, zone[0] if zone else None)
        if not summary:
            return None
        await deps.store.save_profile(
            BusinessProfile(business_id=profile.business_id, **merged.model_dump())
        )
        for fact in found.facts:
            await deps.store.save_lesson(
                Lesson(
                    lesson_id=new_id(),
                    business_id=profile.business_id,
                    team_id=None,
                    kind="fact",
                    text=fact,
                    source="chat",
                    created_at=deps.clock(),
                )
            )
        return PROFILE_UPDATED.format(summary=summary)

    async def chief_reply(state: CompanyState) -> dict:
        profile = await deps.store.get_profile(state["business_id"])
        teams = await deps.store.list_teams(state["business_id"])
        message = state.get("message") or ""
        if profile is not None and message.strip():
            updated = await update_profile(profile, message)
            if updated:
                emit(Say(team_id=None, persona=CHIEF_OF_STAFF, text=updated))
                return {}
        messages = reply_messages(CHIEF_OF_STAFF, profile, teams, list(deps.templates), message)
        reasoning, _ = await needs_reasoning(deps, f"The founder wrote:\n{message}")
        completion = await deps.llm.complete(
            deps.settings.model_lead, messages, reasoning=reasoning
        )
        text = (completion.text or "").strip() or "Got it."
        emit(Say(team_id=None, persona=CHIEF_OF_STAFF, text=text))
        return {}

    graph = StateGraph(CompanyState)
    graph.add_node("entry", entry)
    graph.add_node("question", question)
    graph.add_node("wait", wait)
    graph.add_node("extract", extract)
    graph.add_node("timezone", timezone)
    graph.add_node("finish", finish)
    graph.add_node("chief_reply", chief_reply)
    graph.add_edge(START, "entry")
    graph.add_conditional_edges(
        "entry",
        lambda s: "chief_reply" if s["onboarded"] else "question",
        ["chief_reply", "question"],
    )
    graph.add_edge("question", "wait")
    graph.add_edge("wait", "extract")
    graph.add_conditional_edges("extract", after_extract, ["finish", "question", "timezone"])
    graph.add_edge("timezone", "finish")
    graph.add_edge("finish", END)
    graph.add_edge("chief_reply", END)
    return graph.compile(checkpointer=checkpointer)
