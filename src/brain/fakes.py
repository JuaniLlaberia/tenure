from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from brain.common import new_id, utcnow
from brain.helpers.images import GeneratedImage, ImageError
from brain.helpers.jev import JevAnswer, JevError, JevResponse
from brain.helpers.llm import Completion, LLMError, Message, Structured, ToolDef, Usage
from contract import (
    ActionResult,
    Approval,
    AuditEntry,
    BusinessProfile,
    FileKind,
    FileRef,
    Lesson,
    ModelUsage,
    PageContent,
    Schedule,
    SearchResult,
    Task,
    Team,
    Trust,
)

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T", bound=BaseModel)

def _copy(record: M | None) -> M | None:
    return record.model_copy(deep=True) if record is not None else None

class InMemoryStore:
    """
    The contract Store in memory, for tests and the CLI. Stores and returns copies.
    """

    def __init__(self):
        self.profiles: dict[str, BusinessProfile] = {}
        self.teams: dict[str, Team] = {}
        self.trust: dict[tuple[str, str], Trust] = {}
        self.tasks: dict[str, Task] = {}
        self.approvals: dict[str, Approval] = {}
        self.lessons: dict[str, Lesson] = {}
        self.audit: dict[str, AuditEntry] = {}
        self.usage: list[ModelUsage] = []
        self.schedules: dict[str, Schedule] = {}

    async def get_profile(self, business_id: str) -> BusinessProfile | None:
        return _copy(self.profiles.get(business_id))

    async def save_profile(self, profile: BusinessProfile) -> None:
        self.profiles[profile.business_id] = _copy(profile)

    async def save_team(self, team: Team) -> None:
        self.teams[team.team_id] = _copy(team)

    async def get_team(self, team_id: str) -> Team | None:
        return _copy(self.teams.get(team_id))

    async def list_teams(self, business_id: str) -> list[Team]:
        return [_copy(t) for t in self.teams.values() if t.business_id == business_id]

    async def get_trust(self, team_id: str, task_type: str) -> Trust | None:
        return _copy(self.trust.get((team_id, task_type)))

    async def set_trust(self, trust: Trust) -> None:
        self.trust[(trust.team_id, trust.task_type)] = _copy(trust)

    async def save_task(self, task: Task) -> None:
        self.tasks[task.task_id] = _copy(task)

    async def get_task(self, task_id: str) -> Task | None:
        return _copy(self.tasks.get(task_id))

    async def save_approval(self, approval: Approval) -> None:
        self.approvals[approval.approval_id] = _copy(approval)

    async def get_approval(self, approval_id: str) -> Approval | None:
        return _copy(self.approvals.get(approval_id))

    async def recent_approvals(
        self, team_id: str, task_type: str, limit: int = 5
    ) -> list[Approval]:
        resolved = [
            a
            for a in self.approvals.values()
            if a.team_id == team_id and a.task_type == task_type and a.status != "pending"
        ]
        resolved.sort(key=lambda a: a.resolved_at or a.created_at, reverse=True)
        return [_copy(a) for a in resolved[:limit]]

    async def save_lesson(self, lesson: Lesson) -> None:
        self.lessons[lesson.lesson_id] = _copy(lesson)

    async def list_lessons(
        self, business_id: str, team_id: str | None, task_type: str | None = None
    ) -> list[Lesson]:
        lessons = [
            lesson
            for lesson in self.lessons.values()
            if lesson.active
            and lesson.business_id == business_id
            and lesson.team_id in (None, team_id)
            and (task_type is None or lesson.task_type in (None, task_type))
        ]
        lessons.sort(key=lambda lesson: lesson.created_at, reverse=True)
        return [_copy(lesson) for lesson in lessons]

    async def log_action(self, entry: AuditEntry) -> None:
        self.audit[entry.action_id] = _copy(entry)

    async def get_action(self, action_id: str) -> AuditEntry | None:
        return _copy(self.audit.get(action_id))

    async def log_usage(self, usage: ModelUsage) -> None:
        self.usage.append(_copy(usage))

    async def save_schedule(self, schedule: Schedule) -> None:
        self.schedules[schedule.schedule_id] = _copy(schedule)

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        return _copy(self.schedules.get(schedule_id))

    async def list_schedules(self, business_id: str, team_id: str | None = None) -> list[Schedule]:
        found = [
            schedule
            for schedule in self.schedules.values()
            if schedule.business_id == business_id
            and (team_id is None or schedule.team_id == team_id)
        ]
        found.sort(key=lambda schedule: schedule.created_at, reverse=True)
        return [_copy(schedule) for schedule in found]

class FakeTools:
    """
    The contract Tools without side effects. Records every call; methods in `fail` fail softly.
    """

    def __init__(
        self,
        search_results: list[SearchResult] | None = None,
        pages: dict[str, PageContent] | None = None,
        echo: bool = False,
    ):
        self.search_results = list(search_results or [])
        self.pages = dict(pages or {})
        self.echo = echo
        self.calls: list[tuple[str, dict]] = []
        self.fail: set[str] = set()
        self.files: dict[str, tuple[FileRef, bytes]] = {}
        self._posts = 0

    def _record(self, method: str, **kwargs) -> bool:
        self.calls.append((method, kwargs))
        if self.echo:
            print(f"[fake {method}] {kwargs}")
        return method not in self.fail

    def _failed(self, method: str) -> ActionResult:
        return ActionResult(action_id=new_id(), ok=False, error=f"{method} failed (fake)")

    async def post_social(
        self, business_id: str, text: str, images: list[FileRef] | None = None
    ) -> ActionResult:
        recorded = _with_images({"business_id": business_id, "text": text}, images)
        if not self._record("post_social", **recorded):
            return self._failed("post_social")
        self._posts += 1
        return ActionResult(
            action_id=new_id(),
            ok=True,
            url=f"https://bsky.app/profile/fake/post/{self._posts}",
            external_id=f"at://fake/post/{self._posts}",
        )

    async def delete_social(self, business_id: str, external_id: str) -> ActionResult:
        if not self._record("delete_social", business_id=business_id, external_id=external_id):
            return self._failed("delete_social")
        return ActionResult(action_id=new_id(), ok=True)

    async def send_email(
        self,
        business_id: str,
        to: str,
        subject: str,
        body: str,
        images: list[FileRef] | None = None,
    ) -> ActionResult:
        recorded = {"business_id": business_id, "to": to, "subject": subject, "body": body}
        if not self._record("send_email", **_with_images(recorded, images)):
            return self._failed("send_email")
        return ActionResult(action_id=new_id(), ok=True)

    async def web_search(self, query: str, k: int = 5) -> list[SearchResult]:
        if not self._record("web_search", query=query, k=k):
            return []
        return self.search_results[:k]

    async def fetch_page(self, url: str) -> PageContent | None:
        if not self._record("fetch_page", url=url):
            return None
        return self.pages.get(url)

    def add_file(self, file: FileRef, data: bytes) -> None:
        """
        A file the app already stored, like a founder's upload.
        """
        self.files[file.file_id] = (file, data)

    async def save_file(
        self,
        business_id: str,
        data: bytes,
        mime_type: str,
        name: str | None = None,
        alt_text: str | None = None,
    ) -> FileRef | None:
        saved = self._record(
            "save_file", business_id=business_id, mime_type=mime_type, name=name, alt_text=alt_text
        )
        if not saved:
            return None
        file = FileRef(
            file_id=new_id(),
            business_id=business_id,
            kind=file_kind(mime_type),
            mime_type=mime_type,
            name=name,
            size_bytes=len(data),
            source="generated",
            alt_text=alt_text,
            created_at=utcnow(),
        )
        self.add_file(file, data)
        return file

    async def read_file(self, business_id: str, file_id: str) -> bytes | None:
        if not self._record("read_file", business_id=business_id, file_id=file_id):
            return None
        stored = self.files.get(file_id)
        if stored is None or stored[0].business_id != business_id:
            return None
        return stored[1]

def _with_images(recorded: dict, images: list[FileRef] | None) -> dict:
    """
    Images are recorded only when there are some, so calls without them look as before.
    """
    return {**recorded, "images": list(images)} if images else recorded

def file_kind(mime_type: str) -> FileKind:
    for kind in (FileKind.IMAGE, FileKind.AUDIO, FileKind.VIDEO):
        if mime_type.startswith(f"{kind.value}/"):
            return kind
    return FileKind.DOCUMENT

@dataclass
class LLMCall:
    kind: Literal["complete", "structured"]
    model: str
    messages: list[Message]
    tools: list[ToolDef] | None = None
    schema: type[BaseModel] | None = None
    reasoning: bool | None = None

class FakeLLM:
    """
    Scripted LLM. Structured responses are keyed by schema class name: a response, a list used
    in order (the last repeats), or a callable(messages). A response is a model or a dict.
    """

    def __init__(
        self,
        structured: dict[str, Any] | None = None,
        completions: list[Completion] | Callable[[list[Message]], Completion] | None = None,
        fail: bool = False,
        tokens_per_call: int = 10,
    ):
        self.structured_responses: dict[str, Any] = dict(structured or {})
        self.completions = completions
        self.fail = fail
        self.tokens_per_call = tokens_per_call
        self.calls: list[LLMCall] = []
        self._used: dict[str, int] = defaultdict(int)

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        reasoning: bool | None = None,
    ) -> Completion:
        self.calls.append(
            LLMCall("complete", model, list(messages), tools=tools, reasoning=reasoning)
        )
        if self.fail:
            raise LLMError("fake failure")
        if callable(self.completions):
            completion = self.completions(messages)
        elif self.completions:
            completion = self._next("__complete__", self.completions)
        else:
            completion = Completion(text="Done.")
        return completion.model_copy(update={"tokens": completion.tokens or self.tokens_per_call})

    async def structured(
        self, model: str, messages: list[Message], schema: type[T], reasoning: bool | None = None
    ) -> Structured[T]:
        self.calls.append(
            LLMCall("structured", model, list(messages), schema=schema, reasoning=reasoning)
        )
        if self.fail:
            raise LLMError("fake failure")
        response = self.structured_responses.get(schema.__name__)
        if response is None:
            raise LLMError(f"No scripted response for {schema.__name__}")
        if isinstance(response, list):
            response = self._next(schema.__name__, response)
        elif callable(response) and not isinstance(response, BaseModel):
            response = response(messages)
        value = schema.model_validate(response) if isinstance(response, dict) else response
        return Structured(value=value, tokens=self.tokens_per_call)

    def _next(self, key: str, responses: list):
        index = min(self._used[key], len(responses) - 1)
        self._used[key] += 1
        return responses[index]

class FakeJev:
    """
    Scripted Jev. Answers are keyed by question key: a float (noul), a str (choice at 0.95),
    a dict or JevAnswer, a list used in order (the last repeats), or a callable(state).
    Keys without an answer are left out of the response.
    """

    def __init__(
        self, answers: dict[str, Any] | None = None, fail: bool = False, input_tokens: int = 100
    ):
        self.answers: dict[str, Any] = dict(answers or {})
        self.fail = fail
        self.input_tokens = input_tokens
        self.calls: list[tuple[str, str, dict]] = []
        self._used: dict[str, int] = defaultdict(int)

    async def ask(self, model: str, state: str, questions: dict[str, dict]) -> JevResponse:
        self.calls.append((model, state, questions))
        if self.fail:
            raise JevError("fake failure")
        answers = {}
        for key in questions:
            if key not in self.answers:
                continue
            value = self.answers[key]
            if isinstance(value, list):
                value = value[min(self._used[key], len(value) - 1)]
                self._used[key] += 1
            elif callable(value):
                value = value(state)
            answers[key] = _jev_answer(value)
        return JevResponse(answers=answers, input_tokens=self.input_tokens)

def _jev_answer(value: Any) -> JevAnswer:
    if isinstance(value, JevAnswer):
        return value
    if isinstance(value, dict):
        return JevAnswer.model_validate(value)
    if isinstance(value, str):
        return JevAnswer(type="choice", choice=value, confidence=0.95)
    return JevAnswer(type="noul", noul=float(value))

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000004000000040802000000269309290000001349444154789c"
    "63d48f0d60800126380b2f07002bdb00e4fae499b30000000049454e44ae426082"
)

class FakeImages:
    """
    Scripted image model: always the same 4×4 PNG. Records every call; `fail` raises ImageError.
    """

    def __init__(self, fail: bool = False, cost: float | None = None):
        self.fail = fail
        self.cost = cost
        self.calls: list[dict] = []

    async def generate(
        self,
        model: str,
        prompt: str,
        aspect_ratio: str = "1:1",
        references: list[tuple[bytes, str]] | None = None,
    ) -> GeneratedImage:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "references": list(references or []),
            }
        )
        if self.fail:
            raise ImageError("fake failure")
        usage = Usage(output_tokens=1290, cost=self.cost)
        return GeneratedImage(data=PNG, mime_type="image/png", usage=usage)
