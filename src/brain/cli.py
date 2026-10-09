import asyncio
import mimetypes
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from brain.brain import create_brain
from brain.checkpoint import open_checkpointer
from brain.common import new_id, utcnow
from brain.deps import Settings
from brain.fakes import FakeTools, InMemoryStore, file_kind
from brain.prompts.lead import cadence_words
from contract import (
    ActionDone,
    ActionUndone,
    Approval,
    ApprovalDecision,
    Ask,
    Brain,
    Error,
    Event,
    FileRef,
    IncomingMessage,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    Progress,
    PromotionOffer,
    PromotionResponse,
    Say,
    ScheduleSaved,
    Store,
    TeamHired,
)

HELP = """Commands:
  <text>                 talk in the current topic (General, or the team you switched to)
  /file <path>           attach a local file (photo, voice note, PDF) to your next message;
                         an empty line sends the files alone
  /start                 business onboarding with the chief of staff
  /hire <template>       hire a team (it becomes the current topic)
  /team <name>, /general switch topic
  /approve, /edit <text>, /reject [reason]   answer the latest draft
  /accept, /decline      answer the latest promotion offer
  /undo                  undo the latest post (10 minutes)
  /run <schedule title>  run a schedule now, as the app does when it's due
  /seed <task_type> <n>  demo only: pretend the founder approved n drafts
  /lessons, /tasks, /log show what the store holds
  /help, /quit"""

SEEDED_DRAFT = "We're open this week. Come say hi."

def parse(line: str) -> tuple[str, str]:
    """
    "/name arg" → ("name", "arg"); anything else → ("say", text).
    """
    text = line.strip()
    if not text.startswith("/"):
        return "say", text
    name, _, arg = text[1:].partition(" ")
    return name.lower(), arg.strip()

async def seed_approvals(store: Store, team_id: str, task_type: str, n: int, now: datetime) -> None:
    """
    Demo only (we say so on stage): n approved drafts at high confidence and a streak of n, as
    if the founder had approved them. Reuses the latest real draft so examples stay sensible.
    """
    team = await store.get_team(team_id)
    trust = await store.get_trust(team_id, task_type)
    if team is None or trust is None:
        raise ValueError(f"No {task_type} trust row for team {team_id}")
    recent = await store.recent_approvals(team_id, task_type, limit=1)
    draft = (recent[0].edited_text or recent[0].preview) if recent else SEEDED_DRAFT
    for _ in range(n):
        await store.save_approval(
            Approval(
                approval_id=new_id(),
                business_id=team.business_id,
                team_id=team_id,
                task_id=new_id(),
                task_type=task_type,
                preview=draft,
                planned_action=None,
                check_confidence=0.9,
                status="approved",
                created_at=now,
                resolved_at=now,
            )
        )
    await store.set_trust(trust.model_copy(update={"approval_streak": n, "updated_at": now}))

class Cli:
    """
    A terminal stand-in for Telegram: one business, topics by team, and the latest approval,
    offer and undoable action remembered so commands need no ids.
    """

    def __init__(
        self,
        brain: Brain,
        store: Store,
        business_id: str,
        out: Callable[[str], None] = print,
        tools: FakeTools | None = None,
    ):
        self.brain = brain
        self.store = store
        self.business_id = business_id
        self.out = out
        self.tools = tools
        self.files: list[FileRef] = []
        self.team_id: str | None = None
        self.teams: dict[str, str] = {}
        self.approval_id: str | None = None
        self.offer: tuple[str, str] | None = None
        self.action_id: str | None = None
        self._messages = 0

    def prompt(self) -> str:
        names = {team_id: name for name, team_id in self.teams.items()}
        return f"[{names.get(self.team_id, 'General')}] you> "

    async def handle(self, line: str) -> bool:
        """
        Runs one line. Returns False when the founder quits.
        """
        name, arg = parse(line)
        if name == "quit":
            return False
        if name == "say":
            if arg or self.files:
                await self._drain(self.brain.handle_message(self._message(arg)))
        elif name == "file":
            self._attach(arg)
        elif name == "start":
            await self._drain(self.brain.start_onboarding(self.business_id))
        elif name == "hire":
            await self._drain(self.brain.hire_team(self.business_id, arg or "marketing"))
        elif name == "team":
            await self._switch(arg)
        elif name == "general":
            self.team_id = None
        elif name in ("approve", "edit", "reject"):
            await self._decide(name, arg)
        elif name in ("accept", "decline"):
            await self._promote(name == "accept")
        elif name == "undo":
            await self._undo()
        elif name == "run":
            await self._run_schedule(arg)
        elif name == "seed":
            await self._seed(arg)
        elif name in ("lessons", "tasks", "log"):
            await self._show(name)
        else:
            self.out(HELP)
        return True

    def render(self, event: Event) -> None:
        self._remember(event)
        self.out(self._format(event))

    async def _drain(self, stream) -> None:
        async for event in stream:
            self.render(event)

    def _message(self, text: str) -> IncomingMessage:
        self._messages += 1
        files, self.files = self.files, []
        return IncomingMessage(
            business_id=self.business_id,
            team_id=self.team_id,
            text=text,
            message_id=f"cli-{self._messages}",
            sent_at=utcnow(),
            attachments=files,
        )

    def _attach(self, arg: str) -> None:
        """
        Stores a local file the way the app stores a founder's upload, for the next message.
        """
        path = Path(arg).expanduser()
        if self.tools is None or not arg or not path.is_file():
            self.out("Usage: /file <path to an existing file>")
            return
        data = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file = FileRef(
            file_id=new_id(),
            business_id=self.business_id,
            kind=file_kind(mime_type),
            mime_type=mime_type,
            name=path.name,
            size_bytes=len(data),
            source="founder",
            created_at=utcnow(),
        )
        self.tools.add_file(file, data)
        self.files.append(file)
        self.out(f"(attached {path.name}; it goes with your next message)")

    async def _switch(self, name: str) -> None:
        wanted = name.strip().lower()
        for team in await self.store.list_teams(self.business_id):
            if wanted in (team.display_name.lower(), team.template.lower()):
                self.teams[team.display_name] = team.team_id
                self.team_id = team.team_id
                return
        self.out(f"No team called {name!r}. Hire one with /hire <template>.")

    async def _decide(self, decision: str, arg: str) -> None:
        if self.approval_id is None:
            self.out("There's no draft waiting for you.")
            return
        fields = {"edit": {"edited_text": arg}, "reject": {"reason": arg or None}}
        await self._drain(
            self.brain.resolve_approval(
                ApprovalDecision(
                    business_id=self.business_id,
                    approval_id=self.approval_id,
                    decision=decision,
                    **fields.get(decision, {}),
                )
            )
        )

    async def _promote(self, accepted: bool) -> None:
        if self.offer is None:
            self.out("There's no promotion offer to answer.")
            return
        team_id, task_type = self.offer
        self.offer = None
        await self._drain(
            self.brain.respond_promotion(
                PromotionResponse(
                    business_id=self.business_id,
                    team_id=team_id,
                    task_type=task_type,
                    accepted=accepted,
                )
            )
        )

    async def _undo(self) -> None:
        if self.action_id is None:
            self.out("There's nothing to undo.")
            return
        await self._drain(self.brain.undo_action(self.business_id, self.action_id))

    async def _run_schedule(self, title: str) -> None:
        wanted = title.strip().lower()
        schedules = await self.store.list_schedules(self.business_id)
        match = next((s for s in schedules if wanted and wanted in s.title.lower()), None)
        if match is None:
            self.out("No schedule with that title. Ask a team for one, like \"every Monday …\".")
            return
        await self._drain(self.brain.run_schedule(self.business_id, match.schedule_id))

    async def _seed(self, arg: str) -> None:
        task_type, _, count = arg.partition(" ")
        if self.team_id is None or not count.strip().isdigit():
            self.out("Switch to a team, then: /seed <task_type> <n>")
            return
        await seed_approvals(self.store, self.team_id, task_type, int(count), utcnow())
        self.out(f"(demo) Seeded {count} approved {task_type} drafts; streak is {count}.")

    async def _show(self, what: str) -> None:
        if what == "lessons":
            lessons = await self.store.list_lessons(self.business_id, self.team_id)
            lines = [
                f"- {'[business] ' if lesson.team_id is None else ''}{lesson.text} "
                f"({lesson.source})"
                for lesson in lessons
            ]
        elif what == "tasks":
            tasks = getattr(self.store, "tasks", {}).values()
            lines = [f"- {t.title} [{t.task_type}] {t.status.value}" for t in tasks]
        else:
            entries = getattr(self.store, "audit", {}).values()
            lines = [f"- {e.at:%H:%M} {e.summary} ({e.tool})" for e in entries]
        self.out("\n".join(lines) or "(nothing yet)")

    def _remember(self, event: Event) -> None:
        if isinstance(event, NeedsApproval):
            self.approval_id = event.approval_id
        elif isinstance(event, PromotionOffer):
            self.offer = (event.team_id, event.task_type)
        elif isinstance(event, ActionDone) and event.undo_until is not None:
            self.action_id = event.action_id
        elif isinstance(event, ActionUndone) and event.action_id == self.action_id:
            self.action_id = None
        elif isinstance(event, TeamHired):
            self.teams[event.display_name] = event.team_id
            self.team_id = event.team_id

    def _format(self, event: Event) -> str:
        who = f"{event.persona.name} ({event.persona.role})" if hasattr(event, "persona") else ""
        if isinstance(event, Say):
            return f"{who}: {event.text}{_files(event.media)}"
        if isinstance(event, Progress):
            return f"  … {event.status}"
        if isinstance(event, Ask):
            replies = f"  [{' / '.join(event.quick_replies)}]" if event.quick_replies else ""
            return f"{who}: {event.question}{replies}"
        if isinstance(event, NeedsApproval):
            action = event.planned_action
            files = action.images if action is not None else event.media
            return (
                f"{who}: draft for approval ({event.task_type}, check "
                f"{event.check_confidence:.2f})\n{event.preview}{_files(files)}\n"
                "  → /approve, /edit <text>, /reject [reason]"
            )
        if isinstance(event, ActionDone):
            undo = f"  [undo until {event.undo_until:%H:%M} → /undo]" if event.undo_until else ""
            tag = " (on its own)" if event.autonomous else ""
            return f"✓ {event.summary}{tag}{f' {event.url}' if event.url else ''}{undo}"
        if isinstance(event, ActionUndone):
            return f"↩ {event.summary}"
        if isinstance(event, PromotionOffer):
            return (
                f"{who}: {event.evidence} Move {event.task_type} from {event.current_level} to "
                f"{event.proposed_level}? → /accept or /decline"
            )
        if isinstance(event, LessonLearned):
            scope = " (whole business)" if event.business_wide else ""
            return f"{who}: learned: {event.text}{scope}"
        if isinstance(event, TeamHired):
            people = ", ".join(f"{p.name} ({p.role})" for p in event.personas)
            return f"Hired {event.display_name}: {people}"
        if isinstance(event, ScheduleSaved):
            schedule = event.schedule
            state = "" if schedule.active else " (stopped)"
            return (
                f"{who}: schedule “{schedule.title}”{state}: "
                f"{cadence_words(schedule.cadence)} ({schedule.cadence.timezone}) → /run "
                f"{schedule.title}"
            )
        if isinstance(event, OnboardingComplete):
            return f"(onboarding complete: {event.scope})"
        if isinstance(event, Error):
            return f"! {event.message}"
        return str(event)

def _files(files: list[FileRef]) -> str:
    """
    Images can't show in a terminal: one line per file with what it shows.
    """
    return "".join(f"\n  [image {f.file_id[:8]}: {f.alt_text or f.name or f.kind}]" for f in files)

async def run() -> None:
    settings = Settings.from_env()
    if settings.openrouter_api_key is None:
        print("Set OPENROUTER_API_KEY in .env first.")
        return
    store = InMemoryStore()
    async with open_checkpointer(settings) as checkpointer:
        tools = FakeTools(echo=True)
        brain = create_brain(store, tools, settings, checkpointer=checkpointer)
        cli = Cli(brain, store, business_id=new_id(), tools=tools)
        cli.out(HELP)
        while True:
            try:
                line = await asyncio.to_thread(input, cli.prompt())
            except (EOFError, KeyboardInterrupt):
                break
            if not await cli.handle(line):
                break

def main() -> None:
    asyncio.run(run())

if __name__ == "__main__":
    main()
