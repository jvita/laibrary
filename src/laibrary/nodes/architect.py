"""Architect node - LLM agent that generates document updates."""

from pathlib import Path

import logfire

from ..config import ARCHITECT_SETTINGS, create_agent
from ..prompts import ARCHITECT_SYSTEM_PROMPT
from ..schemas import DocumentUpdate, PKMState


def _build_context_message(target_file: str, context_files: dict[str, str]) -> str:
    """Build a message containing the current document content."""
    from ..document_parser import parse_document

    content = context_files.get(target_file)

    if content:
        msg = f"## Current Document: {target_file}\n\n```markdown\n{content}\n```"

        # Extract and emphasize project-specific instructions
        _, sections = parse_document(content)
        instructions = sections.get("Instructions")
        if instructions:
            msg += (
                "\n\n## Project-Specific Instructions\n\n"
                "The following instructions were written by the user for this "
                "project. Follow them carefully:\n\n"
                f"{instructions}"
            )

        return msg
    else:
        # Extract project name from path for new document
        project_name = target_file.replace("projects/", "").replace(".md", "")
        # Convert kebab-case to Title Case
        title = " ".join(word.capitalize() for word in project_name.split("-"))
        return f"## New Document: {target_file}\n\nThis is a new project document. The title should be: {title}"


def _create_agent():
    """Create the Pydantic-AI agent for document updates."""
    return create_agent(
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        output_type=DocumentUpdate,
        model_settings=ARCHITECT_SETTINGS,
    )


@logfire.instrument("architect_node")
async def architect_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Generate a DocumentUpdate based on user input and context.

    Uses Pydantic-AI with structured output to ensure valid section edits.
    """
    if state.get("error"):
        return state

    note_content = state.get("note_content", "")
    target_project = state.get("target_project", "")
    context_files = state.get("context_files", {})

    if not target_project:
        return {**state, "error": "No target project specified"}

    logfire.info(
        "Generating document update",
        target_file=target_project,
        has_existing_content=target_project in context_files,
    )

    context_message = _build_context_message(target_project, context_files)
    prompt = f"{context_message}\n\n## User Note\n{note_content}"

    agent = _create_agent()

    try:
        result = await agent.run(prompt)
        update = result.output

        # Ensure target_file matches expected project
        if update.target_file != target_project:
            logfire.warn(
                "Architect returned different target file, overriding",
                expected=target_project,
                got=update.target_file,
            )
            update = DocumentUpdate(
                target_file=target_project,
                section_edits=update.section_edits,
                commit_message=update.commit_message,
            )

        logfire.info(
            "Generated update",
            target_file=update.target_file,
            section_count=len(update.section_edits),
            sections=[e.section for e in update.section_edits],
        )
        return {**state, "document_update": update}
    except Exception as e:
        logfire.error("Architect failed", error=str(e))
        return {**state, "error": f"Architect failed: {e}"}
