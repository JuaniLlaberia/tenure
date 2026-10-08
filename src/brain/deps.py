import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr

from brain.common import utcnow
from brain.helpers.jev import Jev
from brain.helpers.llm import LLM
from brain.templates.models import Template
from contract import Store, Tools

class Settings(BaseModel):
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    jev_url: str = "https://openrouter.ai/api/alpha/decisions"
    model_lead: str = "deepseek/deepseek-v4-flash-0731"
    model_specialist: str = "deepseek/deepseek-v4-flash-0731"
    model_reflect: str = "deepseek/deepseek-v4-flash-0731"
    model_decide_fallback: str = "deepseek/deepseek-v4-flash-0731"
    model_jev: str = "typesafe/jev-1.13"
    decide_threshold: float = 0.7
    route_threshold: float = 0.6
    promotion_confidence: float = 0.8
    database_url: SecretStr | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """
        Reads each field from its upper-case env var; empty values keep the default.
        With no mapping, loads `.env` and reads the process environment.
        """
        if env is None:
            load_dotenv()
            env = os.environ
        values = {
            name: env[name.upper()]
            for name in cls.model_fields
            if env.get(name.upper(), "").strip()
        }
        return cls(**values)

@dataclass
class Deps:
    store: Store
    tools: Tools
    llm: LLM
    jev: Jev
    settings: Settings
    templates: dict[str, Template]
    clock: Callable[[], datetime] = utcnow
