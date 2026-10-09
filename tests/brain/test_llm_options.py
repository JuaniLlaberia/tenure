import json

import httpx

from brain.deps import Settings
from brain.helpers.llm import OpenRouterLLM

MESSAGES = [{"role": "user", "content": "Hello"}]

def response():
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

def llm_recording(settings, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterLLM(settings, http_client=client)

def test_reasoning_is_on_and_timeout_set_by_default():
    settings = Settings.from_env({})
    assert settings.llm_reasoning is True
    assert 0 < settings.llm_timeout <= 120

def test_reasoning_reads_env():
    assert Settings.from_env({"LLM_REASONING": "false"}).llm_reasoning is False
    assert Settings.from_env({"LLM_TIMEOUT": "30"}).llm_timeout == 30

async def test_requests_leave_reasoning_to_the_model_by_default():
    requests = []
    llm = llm_recording(Settings(openrouter_api_key="k"), requests)

    await llm.complete("m", MESSAGES)

    assert "reasoning" not in requests[0]

async def test_reasoning_can_be_turned_off():
    requests = []
    llm = llm_recording(Settings(openrouter_api_key="k", llm_reasoning=False), requests)

    await llm.complete("m", MESSAGES)

    assert requests[0]["reasoning"] == {"enabled": False}

async def test_requests_let_openrouter_pick_the_provider_by_default():
    requests = []
    llm = llm_recording(Settings(openrouter_api_key="k"), requests)

    await llm.complete("m", MESSAGES)

    assert "provider" not in requests[0]

async def test_provider_can_be_pinned():
    requests = []
    settings = Settings.from_env({"OPENROUTER_API_KEY": "k", "OPENROUTER_PROVIDER": "alibaba"})
    llm = llm_recording(settings, requests)

    await llm.complete("m", MESSAGES)

    assert requests[0]["provider"] == {"only": ["alibaba"], "allow_fallbacks": False}
