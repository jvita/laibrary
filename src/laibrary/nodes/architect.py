"""Architect node - LLM agent that generates document updates."""

import os
from pathlib import Path

import logfire
from pydantic_ai import Agent

from ..prompts import ARCHITECT_SYSTEM_PROMPT
from ..schemas import DocumentUpdate, PKMState


def _build_context_message(context_files: dict[str, str]) -> str:
    """Build a message containing all current documents."""
    if not context_files:
        return "No existing documents found. You may create a new document."

    parts = ["## Current Documents\n"]
    for filepath, content in sorted(context_files.items()):
        parts.append(f"### {filepath}\n```markdown\n{content}\n```\n")

    return "\n".join(parts)


def _create_agent() -> Agent[None, DocumentUpdate]:
    """Create the Pydantic-AI agent for document updates."""
    return Agent(
        os.environ["MODEL"],
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        output_type=DocumentUpdate,
    )


@logfire.instrument("architect_node")
async def architect_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Generate a DocumentUpdate based on user input and context.

    Uses Pydantic-AI with structured output to ensure valid edits.
    """
    if state.get("error"):
        return state

    user_input = state.get("user_input", "")
    context_files = state.get("context_files", {})
    retry_count = state.get("retry_count", 0)

    logfire.info(
        "Generating document update",
        context_file_count=len(context_files),
        is_retry=retry_count > 0,
    )

    context_message = _build_context_message(context_files)

    prompt = f"{context_message}\n\n## User Note\n{user_input}"

    # Add error feedback if this is a retry
    if state.get("last_edit_error"):
        prompt += f"\n\n## Previous Attempt Failed (retry {retry_count}/{3})\n"
        prompt += f"Error: {state['last_edit_error']}\n"
        if state.get("failed_search_block"):
            prompt += f"\nThe search_block that failed to match:\n```\n{state['failed_search_block']}\n```\n"
        prompt += "\nPlease generate a corrected edit. Ensure the search_block matches the document EXACTLY."

    agent = _create_agent()

    try:
        result = await agent.run(prompt)
        logfire.info("Generated update", target_file=result.output.target_file)
        return {**state, "document_update": result.output}
    except Exception as e:
        logfire.error("Architect failed", error=str(e))
        return {**state, "error": f"Architect failed: {e}"}
