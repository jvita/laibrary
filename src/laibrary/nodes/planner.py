"""Planner node - determines which files need updating."""

import os

import logfire
from pydantic_ai import Agent

from ..config import MAX_RETRIES
from ..prompts import PLANNER_SYSTEM_PROMPT
from ..schemas import PKMState, UpdatePlan


def _build_summaries_message(summaries: dict[str, str]) -> str:
    """Build a message containing document summaries."""
    if not summaries:
        return "No existing documents found."

    parts = ["## Available Documents\n"]
    for filepath, summary in sorted(summaries.items()):
        parts.append(f"- **{filepath}**: {summary}")

    return "\n".join(parts)


def _create_agent() -> Agent[None, UpdatePlan]:
    """Create the Pydantic-AI agent for planning."""
    return Agent(
        os.environ["MODEL"],
        system_prompt=PLANNER_SYSTEM_PROMPT,
        output_type=UpdatePlan,
        retries=MAX_RETRIES,
    )


@logfire.instrument("planner_node")
async def planner_node(state: PKMState) -> PKMState:
    """Determine which files need updating based on user input.

    Creates a structured UpdatePlan that lists which files to modify,
    create, or delete. This plan guides the architect in generating
    the actual edits.
    """
    if state.get("error"):
        return state

    user_input = state.get("user_input", "")
    summaries = state.get("summaries", {})

    logfire.info("Planning multi-document update", doc_count=len(summaries))

    summaries_message = _build_summaries_message(summaries)
    prompt = f"{summaries_message}\n\n## User Note\n{user_input}"

    agent = _create_agent()

    try:
        result = await agent.run(prompt)
        plan = result.output
        logfire.info(
            "Generated update plan",
            file_count=len(plan.file_plans),
            files=[fp.target_file for fp in plan.file_plans],
        )
        return {**state, "update_plan": plan}
    except Exception as e:
        logfire.error("Planner failed", error=str(e))
        return {**state, "error": f"Planner failed: {e}"}
