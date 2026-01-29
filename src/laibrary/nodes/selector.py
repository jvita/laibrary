"""Selector nodes - two-stage context loading with document selection."""

import os
from pathlib import Path

import logfire
from pydantic_ai import Agent

from ..config import MAX_RETRIES, SELECTOR_SETTINGS
from ..git_wrapper import IsolatedGitRepo
from ..prompts import SELECTOR_SYSTEM_PROMPT
from ..schemas import PKMState, SelectionResult
from .summaries import SummaryCache


def summaries_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Load document summaries from cache into state.

    This node loads cached summaries for all markdown files. Summaries that
    are stale (content changed) or missing will not be included.
    """
    if state.get("error"):
        return state

    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)
    cache = SummaryCache(data_dir)

    summaries: dict[str, str] = {}

    for file_path in repo.list_files("**/*.md"):
        content = repo.get_file_content(file_path)
        if content is not None:
            # Try to get cached summary (checks staleness)
            summary = cache.get(file_path, content)
            if summary is not None:
                summaries[file_path] = summary

    logfire.info(
        "Loaded summaries",
        total_files=len(list(repo.list_files("**/*.md"))),
        cached_summaries=len(summaries),
    )

    return {**state, "summaries": summaries}


async def selector_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Run selector agent to pick relevant documents.

    Uses summaries from state to decide which documents are relevant to
    the user's request. Falls back to loading all documents if:
    - No summaries are available
    - Selector returns empty list
    - Selector fails
    """
    if state.get("error"):
        return state

    summaries = state.get("summaries", {})
    user_input = state.get("user_input", "")

    # Fallback: no summaries available, load all docs
    if not summaries:
        logfire.info("No summaries available, will load all documents")
        return {**state, "selected_files": None}

    # Build prompt with summaries
    summary_parts = ["## Available Documents\n"]
    for file_path, summary in summaries.items():
        summary_parts.append(f"- **{file_path}**: {summary}")
    summary_text = "\n".join(summary_parts)

    prompt = f"{summary_text}\n\n## User Request\n{user_input}"

    # Run selector agent
    try:
        agent = Agent(
            os.environ["MODEL"],
            system_prompt=SELECTOR_SYSTEM_PROMPT,
            output_type=SelectionResult,
            retries=MAX_RETRIES,
            model_settings=SELECTOR_SETTINGS,
        )
        result = await agent.run(prompt)
        selection = result.output

        logfire.info(
            "Selector decision",
            selected_count=len(selection.selected_files),
            reasoning=selection.reasoning,
        )

        # Fallback: empty selection means load all
        if not selection.selected_files:
            logfire.info("Selector returned empty list, will load all documents")
            return {**state, "selected_files": None}

        return {**state, "selected_files": selection.selected_files}

    except Exception as e:
        logfire.error("Selector failed, will load all documents", error=str(e))
        return {**state, "selected_files": None}
