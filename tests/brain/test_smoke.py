import importlib

import pytest

@pytest.mark.parametrize(
    "module",
    [
        "brain",
        "brain.graphs",
        "brain.flows",
        "brain.templates",
        "brain.prompts",
        "langgraph",
        "openai",
        "httpx",
        "yaml",
        "dotenv",
    ],
)
def test_module_imports(module):
    importlib.import_module(module)
