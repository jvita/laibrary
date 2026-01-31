"""Committer node - applies section edits and commits."""

from pathlib import Path

import logfire

from ..document_parser import (
    apply_edits,
    parse_document,
    render_document,
)
from ..git_wrapper import IsolatedGitRepo
from ..schemas import PKMState


@logfire.instrument("committer_node")
async def committer_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Apply section edits and commit to git.

    Reads the current document, applies section edits using the parser,
    and commits the result.
    """
    if state.get("error"):
        return state

    update = state.get("document_update")
    if not update:
        return {**state, "error": "No document update to apply"}

    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)

    logfire.info(
        "Applying section edits",
        target_file=update.target_file,
        edit_count=len(update.section_edits),
    )

    # Get current content (may be None for new files)
    existing_content = repo.get_file_content(update.target_file)

    try:
        if existing_content:
            # Parse existing document
            title, sections = parse_document(existing_content)
        else:
            # New document - extract title from file path
            project_name = update.target_file.replace("projects/", "").replace(
                ".md", ""
            )
            title = " ".join(word.capitalize() for word in project_name.split("-"))
            sections = {}

        # Apply section edits
        updated_sections = apply_edits(sections, update.section_edits)

        # Render updated document
        new_content = render_document(title, updated_sections)

        # Write and commit
        repo.write_file(update.target_file, new_content)
        repo.add_and_commit(update.target_file, update.commit_message)

        logfire.info("Committed changes", commit_message=update.commit_message)

        return {**state, "committed": True}

    except Exception as e:
        error_msg = f"Failed to apply edits to {update.target_file}: {e}"
        logfire.error(error_msg, exc_info=True)
        return {**state, "error": error_msg}
