"""Committer node - applies edits atomically and commits."""

from pathlib import Path

import logfire

from ..git_wrapper import IsolatedGitRepo
from ..schemas import DocumentUpdate, PKMState
from .summaries import SummaryCache, generate_summary


def _get_new_content(update: DocumentUpdate) -> str:
    """Get new content from update."""
    return update.full_content


async def _commit_multi(
    updates: list[DocumentUpdate],
    commit_message: str,
    repo: IsolatedGitRepo,
    data_dir: Path,
) -> tuple[bool, str | None, int | None]:
    """Apply multiple document updates atomically.

    All updates are validated in memory before any writes occur.
    Returns (success, error_message, failed_file_index).
    """
    # Step 1: Validate ALL updates in memory
    validated_contents: dict[str, str] = {}
    files_to_delete: list[str] = []

    for idx, update in enumerate(updates):
        try:
            if update.delete_file:
                # Validate file exists for deletion
                if not repo.file_exists(update.target_file):
                    return (
                        False,
                        f"Cannot delete {update.target_file}: file not found",
                        idx,
                    )
                files_to_delete.append(update.target_file)
            else:
                # Check if file exists
                existing_content = repo.get_file_content(update.target_file)

                if existing_content is None:
                    if not update.create_if_missing:
                        return (
                            False,
                            f"File '{update.target_file}' does not exist and create_if_missing is False",
                            idx,
                        )

                    # Guardrail: Only allow new file creation under projects/
                    if not update.target_file.startswith("projects/"):
                        return (
                            False,
                            f"New files can only be created under 'projects/' directory. Cannot create: {update.target_file}",
                            idx,
                        )

                # Get new content
                new_content = _get_new_content(update)
                validated_contents[update.target_file] = new_content

        except Exception as e:
            return (
                False,
                f"Unexpected error validating {update.target_file}: {e}",
                idx,
            )

    # Step 2: All validations passed - write all files
    files_written: list[str] = []

    try:
        # Delete files
        for file_path in files_to_delete:
            repo.delete_file(file_path)
            files_written.append(file_path)

        # Write new/modified files
        for file_path, content in validated_contents.items():
            repo.write_file(file_path, content)
            files_written.append(file_path)

        # Step 3: Single commit for all changes
        repo.add_and_commit_multiple(files_written, commit_message, files_to_delete)

        # Step 4: Update summary cache for all modified files
        cache = SummaryCache(data_dir)
        for file_path in files_to_delete:
            cache.remove(file_path)

        for file_path, content in validated_contents.items():
            try:
                summary = await generate_summary(content)
                cache.set(file_path, content, summary)
                logfire.info("Generated summary for updated file", file=file_path)
            except Exception as e:
                # Summary generation is non-critical
                logfire.warn("Failed to generate summary", file=file_path, error=str(e))

        return (True, None, None)

    except Exception as e:
        # This should rarely happen since we validated everything
        return (False, f"Failed to write/commit changes: {e}", None)


@logfire.instrument("committer_node")
async def committer_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Apply document edits and commit to git.

    All edits are applied in memory first. If any edit fails,
    no changes are written to disk.

    Supports both single-file and multi-file updates.
    """
    if state.get("error"):
        return state

    # Check for multi-document updates first
    updates = state.get("document_updates")
    if updates:
        return await _handle_multi_update(state, updates, data_dir)

    # Fall back to single-file update
    update = state.get("document_update")
    if not update:
        return {**state, "error": "No document update to apply"}

    return await _handle_single_update(state, update, data_dir)


async def _handle_single_update(
    state: PKMState, update: DocumentUpdate, data_dir: Path | None
) -> PKMState:
    """Handle a single document update (backward compatibility)."""
    logfire.info(
        "Applying update",
        target_file=update.target_file,
    )

    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)

    # Check if this is a deletion operation
    if update.delete_file:
        try:
            repo.delete_file(update.target_file)
            commit_sha = repo.add_and_commit(
                update.target_file, update.commit_message, is_deletion=True
            )
            logfire.info(
                "File deleted and committed",
                file=update.target_file,
                commit=commit_sha,
            )
            # Remove from summary cache
            cache = SummaryCache(data_dir)
            cache.remove(update.target_file)
            return {**state, "committed": True}
        except FileNotFoundError:
            error_msg = f"Cannot delete {update.target_file}: file not found"
            logfire.error(error_msg)
            return {**state, "error": error_msg}
        except Exception as e:
            error_msg = f"Failed to delete {update.target_file}: {e}"
            logfire.error(error_msg, exc_info=True)
            return {**state, "error": error_msg}

    # Check if file exists
    existing_content = repo.get_file_content(update.target_file)

    if existing_content is None:
        if not update.create_if_missing:
            return {
                **state,
                "error": f"File '{update.target_file}' does not exist and create_if_missing is False",
            }

        # Guardrail: Only allow new file creation under projects/
        if not update.target_file.startswith("projects/"):
            error_msg = f"New files can only be created under 'projects/' directory. Cannot create: {update.target_file}"
            logfire.error(error_msg)
            return {**state, "error": error_msg}

    # Get new content
    try:
        new_content = _get_new_content(update)
    except Exception as e:
        # Store error details for potential retry
        logfire.warn("Content retrieval failed", error=str(e))
        return {
            **state,
            "error": str(e),
            "last_edit_error": str(e),
        }

    # Write and commit
    repo.write_file(update.target_file, new_content)
    repo.add_and_commit(update.target_file, update.commit_message)

    logfire.info("Committed changes", commit_message=update.commit_message)

    # Generate and cache summary for the updated file
    try:
        cache = SummaryCache(data_dir)
        summary = await generate_summary(new_content)
        cache.set(update.target_file, new_content, summary)
        logfire.info("Generated summary for updated file", file=update.target_file)
    except Exception as e:
        # Summary generation is non-critical, log and continue
        logfire.warn(
            "Failed to generate summary", file=update.target_file, error=str(e)
        )

    return {**state, "committed": True}


async def _handle_multi_update(
    state: PKMState, updates: list[DocumentUpdate], data_dir: Path | None
) -> PKMState:
    """Handle multiple document updates atomically."""
    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)

    # Get commit message from update_plan or generate one
    update_plan = state.get("update_plan")
    commit_message = (
        update_plan.commit_message if update_plan else "Update multiple documents"
    )

    logfire.info(
        "Applying multi-document update",
        update_count=len(updates),
        files=[u.target_file for u in updates],
    )

    success, error_msg, failed_idx = await _commit_multi(
        updates, commit_message, repo, data_dir
    )

    if success:
        logfire.info("Multi-document commit succeeded", commit_message=commit_message)
        return {**state, "committed": True}
    else:
        # Store error details for retry
        logfire.warn(
            "Multi-document update failed",
            error=error_msg,
            failed_file_index=failed_idx,
        )

        return {
            **state,
            "error": error_msg,
            "last_edit_error": error_msg,
            "retry_file_index": failed_idx,
        }
