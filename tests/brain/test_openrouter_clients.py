import json

import httpx
import pytest
from pydantic import BaseModel

from brain.deps import Settings
from brain.helpers.jev import JevError, OpenRouterJev
from brain.helpers.llm import LLMError, OpenRouterLLM, ToolDef

SETTINGS = Settings(openrouter_api_key="sk-test-key")
MESSAGES = [{"role": "user", "content": "Hello"}]

JEV_RESPONSE = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "has_feedback": {"type": "noul", "noul": 0.97},
        "route": {
            "type": "choice",
            "choice": "social_post",
            "probabilities": {"social_post": 0.98, "newsletter": 0, "none": 0.02},
            "confidence": 0.98,
        },
    },
    "usage": {"input_tokens": 382, "output_tokens": 57, "cost": 0.000016044},
    "id": "gen-dec-1",
    "provider": "TypeSafe",
}

class Capital(BaseModel):
    city: str

def chat_response(content=None, tool_calls=None, total_tokens=8, cost=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": total_tokens}
    if cost is not None:
        usage["cost"] = cost
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek/deepseek-v4-flash-0731",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": usage,
    }

def llm_with(responses, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterLLM(SETTINGS, http_client=client)

async def test_jev_request_and_response():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=JEV_RESPONSE)

    jev = OpenRouterJev(SETTINGS, transport=httpx.MockTransport(handler))
    questions = {"has_feedback": {"type": "noul", "instructions": "Feedback?", "criteria": {}}}
    response = await jev.ask("typesafe/jev-1.13", "Stop using hashtags", questions)

    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
    assert request.headers["authorization"] == "Bearer sk-test-key"
    assert json.loads(request.content) == {
        "model": "typesafe/jev-1.13",
        "state": "Stop using hashtags",
        "questions": questions,
    }
    assert response.answers["has_feedback"].noul == 0.97
    assert response.answers["route"].choice == "social_post"
    assert response.answers["route"].confidence == 0.98
    assert response.input_tokens == 382
    assert response.output_tokens == 57
    assert response.cost == 0.000016044

async def test_jev_http_error_raises_without_leaking_key():
    jev = OpenRouterJev(
        SETTINGS, transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    )
    with pytest.raises(JevError) as error:
        await jev.ask("typesafe/jev-1.13", "state", {})
    assert "sk-test-key" not in str(error.value)

async def test_jev_network_error_raises():
    def handler(request):
        raise httpx.ConnectError("down")

    jev = OpenRouterJev(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(JevError):
        await jev.ask("typesafe/jev-1.13", "state", {})

async def test_jev_without_key_raises():
    with pytest.raises(JevError):
        await OpenRouterJev(Settings()).ask("typesafe/jev-1.13", "state", {})

async def test_llm_complete_parses_tool_calls():
    requests = []
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "web_search", "arguments": '{"query": "launch ideas"}'},
    }
    llm = llm_with([(200, chat_response(tool_calls=[tool_call]))], requests)
    tool = ToolDef(
        name="web_search",
        description="Search the web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )

    completion = await llm.complete("deepseek/deepseek-v4-flash-0731", MESSAGES, [tool])

    assert completion.text is None
    assert completion.tool_calls[0].name == "web_search"
    assert completion.tool_calls[0].arguments == {"query": "launch ideas"}
    assert completion.tokens == 8
    assert requests[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": tool.parameters,
            },
        }
    ]

async def test_llm_structured_parses_json():
    requests = []
    llm = llm_with([(200, chat_response(content='{"city": "Paris"}'))], requests)
    result = await llm.structured("m", MESSAGES, Capital)
    assert result.value == Capital(city="Paris")
    assert requests[0]["response_format"]["type"] == "json_schema"

async def test_llm_structured_retries_once_on_invalid_json():
    requests = []
    llm = llm_with(
        [
            (200, chat_response(content="Paris!", total_tokens=8, cost=0.001)),
            (200, chat_response(content='{"city": "Paris"}', total_tokens=9, cost=0.002)),
        ],
        requests,
    )
    result = await llm.structured("m", MESSAGES, Capital)
    assert result.value == Capital(city="Paris")
    assert result.tokens == 17
    assert (result.usage.input_tokens, result.usage.output_tokens) == (10, 6)
    assert result.usage.cost == pytest.approx(0.003)
    assert len(requests) == 2

async def test_llm_structured_raises_after_second_failure():
    llm = llm_with(
        [(200, chat_response(content="nope")), (200, chat_response(content="still nope"))], []
    )
    with pytest.raises(LLMError):
        await llm.structured("m", MESSAGES, Capital)

async def test_llm_api_error_becomes_llm_error():
    llm = llm_with([(400, {"error": {"message": "bad request"}})], [])
    with pytest.raises(LLMError):
        await llm.complete("m", MESSAGES)
