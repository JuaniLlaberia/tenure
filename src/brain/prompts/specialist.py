from pydantic import BaseModel

from brain.helpers.llm import Message
from contract import Persona

def specialist_messages(
    persona: Persona, task_type: str, output: str, brief: str, context: str, guidance: str = ""
) -> list[Message]:
    if output == "notes":
        result = "Your result is notes for the next step: what you found, with the sources."
    else:
        result = f"Your result is the finished {output.replace('_', ' ')}, ready for the founder."
    if guidance:
        result += f" It must be: {guidance}"
    system = (
        f"You are {persona.name}, the {persona.role} on a small business's team. "
        "You do one step of a task, then hand your result back to the lead. "
        "Use your tools when they help, and stop calling them once you have what you need. "
        "Never invent facts, prices, names, dates or links that aren't in the brief, the context "
        "or your research. You never publish or send anything; the founder reviews it first. "
        f"{result}"
    )
    user = f"Task ({task_type}): {brief}"
    if context:
        user += f"\n\n{context}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def final_message(schema: type[BaseModel]) -> Message:
    fields = ", ".join(schema.model_fields)
    return {
        "role": "user",
        "content": f"Now give your final result as JSON with the fields: {fields}. JSON only.",
    }
