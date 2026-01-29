"""Architect node - LLM agent that generates document updates."""

import os
from pathlib import Path

import logfire
from pydantic_ai import Agent

from ..prompts import ARCHITECT_MULTI_SYSTEM_PROMPT, ARCHITECT_SYSTEM_PROMPT
from ..schemas import DocumentUpdate, MultiDocumentUpdate, PKMState


def _build_context_message(context_files: dict[str, str]) -> str:
    """Build a message containing all current documents."""
    if not context_files:
        return "No existing documents found. You may create a new document."

    parts = ["## Current Documents\n"]
    for filepath, content in sorted(context_files.items()):
        parts.append(f"### {filepath}\n```markdown\n{content}\n```\n")

    return "\n".join(parts)


def _build_plan_message(plan) -> str:
    """Build a message describing the update plan."""
    parts = ["## Update Plan\n"]
    parts.append(f"**Reasoning**: {plan.reasoning}\n")
    parts.append(f"**Commit Message**: {plan.commit_message}\n")
    parts.append("\n**Files to Update**:")

    for file_plan in plan.file_plans:
        parts.append(
            f"- **{file_plan.target_file}** ({file_plan.action}): {file_plan.description}"
        )

    return "\n".join(parts)


def _create_agent() -> Agent[None, DocumentUpdate]:
    """Create the Pydantic-AI agent for document updates."""
    return Agent(
        os.environ["MODEL"],
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        output_type=DocumentUpdate,
    )


def _create_multi_agent() -> Agent[None, MultiDocumentUpdate]:
    """Create the Pydantic-AI agent for multi-document updates."""
    return Agent(
        os.environ["MODEL"],
        system_prompt=ARCHITECT_MULTI_SYSTEM_PROMPT,
        output_type=MultiDocumentUpdate,
    )


@logfire.instrument("architect_node")
async def architect_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Generate a DocumentUpdate based on user input and context.

    Uses Pydantic-AI with structured output to ensure valid edits.
    Supports both single-file and multi-file updates based on state.
    """
    if state.get("error"):
        return state

    user_input = state.get("user_input", "")
    context_files = state.get("context_files", {})
    retry_count = state.get("retry_count", 0)
    update_plan = state.get("update_plan")

    # Determine if this is a multi-document update
    is_multi = update_plan is not None

    logfire.info(
        "Generating document update",
        context_file_count=len(context_files),
        is_retry=retry_count > 0,
        is_multi_update=is_multi,
    )

    context_message = _build_context_message(context_files)

    # Build the prompt
    if is_multi:
        plan_message = _build_plan_message(update_plan)
        prompt = f"{context_message}\n\n{plan_message}\n\n## User Note\n{user_input}"
    else:
        prompt = f"{context_message}\n\n## User Note\n{user_input}"

    # Add error feedback if this is a retry
    if state.get("last_edit_error"):
        from .. import MAX_RETRIES

        prompt += (
            f"\n\n## Previous Attempt Failed (retry {retry_count}/{MAX_RETRIES})\n"
        )
        prompt += f"Error: {state['last_edit_error']}\n"
        if state.get("failed_search_block"):
            prompt += f"\nThe search_block that failed to match:\n```\n{state['failed_search_block']}\n```\n"
        if state.get("retry_file_index") is not None:
            prompt += f"\nThe error occurred in file index {state['retry_file_index']} ({update_plan.file_plans[state['retry_file_index']].target_file if update_plan else 'unknown'})\n"
        prompt += "\nPlease generate a corrected edit. Ensure the search_block matches the document EXACTLY."

    # Choose the appropriate agent
    if is_multi:
        agent = _create_multi_agent()
    else:
        agent = _create_agent()

    try:
        result = await agent.run(prompt)
        if is_multi:
            logfire.info(
                "Generated multi-document update",
                update_count=len(result.output.updates),
                files=[u.target_file for u in result.output.updates],
            )
            return {**state, "document_updates": result.output.updates}
        else:
            logfire.info("Generated update", target_file=result.output.target_file)
            return {**state, "document_update": result.output}
    except Exception as e:
        logfire.error("Architect failed", error=str(e))
        return {**state, "error": f"Architect failed: {e}"}
