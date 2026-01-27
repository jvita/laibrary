"""Committer node - applies edits atomically and commits."""

from pathlib import Path

import logfire

from ..exceptions import EditApplicationError
from ..git_wrapper import IsolatedGitRepo
from ..schemas import DocumentUpdate, PKMState


def _apply_edits(content: str, update: DocumentUpdate) -> str:
    """Apply all edits to content in memory.

    Raises EditApplicationError if any edit fails.
    """
    result = content

    for i, edit in enumerate(update.edits):
        if edit.search_block == "":
            # Empty search block means append/create
            result = result + edit.replace_block
        elif edit.search_block not in result:
            raise EditApplicationError(
                f"Edit {i + 1}: search_block not found in document",
                update.target_file,
                edit.search_block,
            )
        elif result.count(edit.search_block) > 1:
            raise EditApplicationError(
                f"Edit {i + 1}: search_block appears multiple times ({result.count(edit.search_block)} occurrences)",
                update.target_file,
                edit.search_block,
            )
        else:
            result = result.replace(edit.search_block, edit.replace_block, 1)

    return result


@logfire.instrument("committer_node")
def committer_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Apply document edits and commit to git.

    All edits are applied in memory first. If any edit fails,
    no changes are written to disk.
    """
    if state.get("error"):
        return state

    update = state.get("document_update")
    if not update:
        return {**state, "error": "No document update to apply"}

    logfire.info(
        "Applying edits",
        target_file=update.target_file,
        edit_count=len(update.edits),
    )

    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)

    # Read existing content or start empty
    existing_content = repo.get_file_content(update.target_file)

    if existing_content is None:
        if not update.create_if_missing:
            return {
                **state,
                "error": f"File '{update.target_file}' does not exist and create_if_missing is False",
            }
        existing_content = ""

    # Apply all edits in memory
    try:
        new_content = _apply_edits(existing_content, update)
    except EditApplicationError as e:
        # Store error details for potential retry
        logfire.warn(
            "Edit application failed",
            error=str(e),
            search_block_preview=e.search_block[:100] if e.search_block else None,
        )
        return {
            **state,
            "error": str(e),
            "last_edit_error": str(e),
            "failed_search_block": e.search_block,
        }

    # Write and commit
    repo.write_file(update.target_file, new_content)
    repo.add_and_commit(update.target_file, update.commit_message)

    logfire.info("Committed changes", commit_message=update.commit_message)
    return {**state, "committed": True}
