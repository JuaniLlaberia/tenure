import pytest
from pydantic import BaseModel

from brain.fakes import FakeJev, FakeLLM
from brain.helpers.jev import JevAnswer, JevError
from brain.helpers.llm import Completion, LLMError, ToolCall, ToolDef

class Answer(BaseModel):
    value: int

MESSAGES = [{"role": "user", "content": "Hello"}]

async def test_structured_responses_by_schema_name_in_order():
    llm = FakeLLM(structured={"Answer": [Answer(value=1), {"value": 2}]})
    values = [(await llm.structured("m", MESSAGES, Answer)).value.value for _ in range(3)]
    assert values == [1, 2, 2]

async def test_structured_accepts_dicts_and_callables():
    llm = FakeLLM(structured={"Answer": lambda messages: {"value": len(messages)}})
    result = await llm.structured("m", MESSAGES, Answer)
    assert result.value == Answer(value=1)
    assert result.tokens == llm.tokens_per_call

    llm = FakeLLM(structured={"Answer": {"value": 7}})
    assert (await llm.structured("m", MESSAGES, Answer)).value == Answer(value=7)

async def test_missing_structured_response_raises_llm_error():
    with pytest.raises(LLMError):
        await FakeLLM().structured("m", MESSAGES, Answer)

async def test_completions_default_to_no_tool_calls():
    completion = await FakeLLM().complete("m", MESSAGES)
    assert completion.text
    assert completion.tool_calls == []

async def test_completions_in_order():
    call = ToolCall(id="c1", name="web_search", arguments={"query": "launch"})
    llm = FakeLLM(completions=[Completion(text=None, tool_calls=[call]), Completion(text="Done")])
    first = await llm.complete("m", MESSAGES)
    second = await llm.complete("m", MESSAGES)
    third = await llm.complete("m", MESSAGES)
    assert first.tool_calls == [call]
    assert second.text == "Done"
    assert third.text == "Done"

async def test_calls_are_recorded():
    tool = ToolDef(name="web_search", description="Search", parameters={"type": "object"})
    llm = FakeLLM(structured={"Answer": {"value": 1}})
    await llm.complete("model-a", MESSAGES, [tool])
    await llm.structured("model-b", MESSAGES, Answer)

    complete, structured = llm.calls
    assert (complete.kind, complete.model, complete.messages, complete.tools) == (
        "complete",
        "model-a",
        MESSAGES,
        [tool],
    )
    assert (structured.kind, structured.model, structured.schema) == (
        "structured",
        "model-b",
        Answer,
    )

async def test_fail_raises_llm_error():
    llm = FakeLLM(fail=True)
    with pytest.raises(LLMError):
        await llm.complete("m", MESSAGES)
    with pytest.raises(LLMError):
        await llm.structured("m", MESSAGES, Answer)

async def test_fake_jev_shorthands():
    calls = iter([0.1, 0.9])
    jev = FakeJev(
        answers={
            "noul": 0.8,
            "choice": "newsletter",
            "full": {"type": "choice", "choice": "none", "confidence": 0.6},
            "listed": [0.2, 0.7],
            "computed": lambda state: next(calls),
        }
    )
    questions = {key: {} for key in ["noul", "choice", "full", "listed", "computed", "missing"]}

    first = await jev.ask("m", "state", questions)
    assert first.answers["noul"] == JevAnswer(type="noul", noul=0.8)
    assert first.answers["choice"] == JevAnswer(type="choice", choice="newsletter", confidence=0.95)
    assert first.answers["full"].confidence == 0.6
    assert first.answers["listed"].noul == 0.2
    assert first.answers["computed"].noul == 0.1
    assert "missing" not in first.answers
    assert first.input_tokens == jev.input_tokens

    second = await jev.ask("m", "state", questions)
    assert second.answers["listed"].noul == 0.7
    assert second.answers["computed"].noul == 0.9
    assert jev.calls[0] == ("m", "state", questions)

async def test_fake_jev_fail_raises():
    with pytest.raises(JevError):
        await FakeJev(fail=True).ask("m", "state", {})
