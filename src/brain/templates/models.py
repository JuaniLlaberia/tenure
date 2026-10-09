from pydantic import BaseModel, Field, field_validator, model_validator

from brain.templates.registries import ACTIONS, OUTPUTS, TOOL_NAMES
from contract import AutonomyLevel, Persona

MAX_ONBOARDING_QUESTIONS = 3

LADDER = list(AutonomyLevel)

class LeadSpec(BaseModel):
    persona: Persona
    instructions: str

class SpecialistSpec(BaseModel):
    persona: Persona
    tools: list[str] = []
    instructions: str = ""

class TaskTypeSpec(BaseModel):
    description: str
    steps: list[str] = Field(min_length=1)
    output: str
    check: list[str] = []
    action: str | None = None
    start_level: AutonomyLevel = AutonomyLevel.ACT_AFTER_APPROVAL
    max_level: AutonomyLevel | None = None

class OnboardingQuestion(BaseModel):
    question: str
    quick_replies: list[str] = []

class Limits(BaseModel):
    max_tasks_per_request: int = 3
    max_steps_per_specialist: int = 6
    token_budget: int = 60000

class Template(BaseModel):
    name: str
    display_name: str
    description: str
    lead: LeadSpec
    specialists: dict[str, SpecialistSpec]
    task_types: dict[str, TaskTypeSpec]
    onboarding: dict[str, OnboardingQuestion] = {}
    channel_question: OnboardingQuestion | None = None
    limits: Limits = Limits()

    @field_validator("onboarding", mode="before")
    @classmethod
    def _plain_questions(cls, onboarding: dict) -> dict:
        """
        A question in YAML is a string, or a mapping with `question` and `quick_replies`.
        """
        return {
            key: {"question": value} if isinstance(value, str) else value
            for key, value in (onboarding or {}).items()
        }

    def max_level(self, task_type: str) -> AutonomyLevel:
        spec = self.task_types[task_type]
        return spec.max_level or spec.start_level

    def channel_task_types(self) -> list[str]:
        """
        Task types that go out somewhere (they have an action), like a post or an email.
        """
        return [name for name, spec in self.task_types.items() if spec.action]

    def personas(self) -> list[Persona]:
        return [self.lead.persona] + [spec.persona for spec in self.specialists.values()]

    @model_validator(mode="after")
    def _check_names(self) -> "Template":
        for specialist_id, spec in self.specialists.items():
            unknown = set(spec.tools) - TOOL_NAMES
            if unknown:
                raise ValueError(
                    f"Specialist {specialist_id!r} has unknown tools: {sorted(unknown)}"
                )
        for task_type in self.task_types:
            self._check_task_type(task_type)
        if len(self.onboarding) > MAX_ONBOARDING_QUESTIONS:
            raise ValueError(f"At most {MAX_ONBOARDING_QUESTIONS} onboarding questions")
        return self

    def _check_task_type(self, task_type: str) -> None:
        spec = self.task_types[task_type]
        unknown = [step for step in spec.steps if step not in self.specialists]
        if unknown:
            raise ValueError(f"Task type {task_type!r} has unknown steps: {unknown}")
        if spec.output not in OUTPUTS:
            raise ValueError(f"Task type {task_type!r} has unknown output {spec.output!r}")
        if spec.action is not None:
            if spec.action not in ACTIONS:
                raise ValueError(f"Task type {task_type!r} has unknown action {spec.action!r}")
            if ACTIONS[spec.action] != spec.output:
                raise ValueError(
                    f"Task type {task_type!r}: action {spec.action!r} needs output "
                    f"{ACTIONS[spec.action]!r}, not {spec.output!r}"
                )
        if LADDER.index(spec.start_level) > LADDER.index(self.max_level(task_type)):
            raise ValueError(f"Task type {task_type!r} starts above its max level")
