"""Committer node - applies edits atomically and commits."""

from pathlib import Path

import logfire

from ..exceptions import EditApplicationError
from ..git_wrapper import IsolatedGitRepo
from ..schemas import DocumentUpdate, PKMState
from .summaries import SummaryCache, generate_summary


def _find_similar_lines(
    content: str, search_block: str, max_suggestions: int = 5
) -> list[str]:
    """Find lines in content that are similar to the search block.

    Helps provide actionable feedback when search_block doesn't match exactly.
    """
    # Get first few words from search block for fuzzy matching
    search_words = search_block.strip().split()[:5]
    if not search_words:
        return []

    lines = content.split("\n")
    suggestions = []

    for line_num, line in enumerate(lines, 1):
        # Check if any search words appear in this line
        if any(word.lower() in line.lower() for word in search_words):
            suggestions.append(f"Line {line_num}: {line[:80]}")
            if len(suggestions) >= max_suggestions:
                break

    return suggestions


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
            # Provide helpful context about what IS in the document
            similar_lines = _find_similar_lines(result, edit.search_block)

            error_msg = f"Edit {i + 1}: search_block not found in document"
            if similar_lines:
                error_msg += "\n\nSimilar lines found:\n" + "\n".join(similar_lines)
                error_msg += "\n\nEnsure exact character-by-character match including whitespace, line breaks, and punctuation."
            else:
                # Show a preview of document structure
                lines = result.split("\n")
                preview_lines = [
                    f"Line {i+1}: {line[:60]}" for i, line in enumerate(lines[:10])
                ]
                error_msg += "\n\nDocument starts with:\n" + "\n".join(preview_lines)

            raise EditApplicationError(
                error_msg,
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


async def _commit_multi(
    updates: list[DocumentUpdate],
    commit_message: str,
    repo: IsolatedGitRepo,
    data_dir: Path,
) -> tuple[bool, str | None, int | None]:
    """Apply multiple document updates atomically.

    All edits are validated in memory before any writes occur.
    Returns (success, error_message, failed_file_index).
    """
    # Step 1: Validate ALL edits in memory
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
                # Read existing content or start empty
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

                    existing_content = ""

                # Apply edits in memory
                new_content = _apply_edits(existing_content, update)
                validated_contents[update.target_file] = new_content

        except EditApplicationError as e:
            return (False, str(e), idx)
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
        "Applying edits",
        target_file=update.target_file,
        edit_count=len(update.edits),
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

    # Read existing content or start empty
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

        # Try to extract the failed search block for better retry context
        failed_search_block = None
        if failed_idx is not None and "search_block not found" in (error_msg or ""):
            # Parse error message to find which edit failed (e.g., "Edit 2:")
            import re

            edit_match = re.search(r"Edit (\d+):", error_msg or "")
            if edit_match and updates[failed_idx].edits:
                edit_num = int(edit_match.group(1)) - 1  # Convert to 0-indexed
                try:
                    if 0 <= edit_num < len(updates[failed_idx].edits):
                        failed_search_block = (
                            updates[failed_idx].edits[edit_num].search_block
                        )
                except (IndexError, AttributeError):
                    pass

            # Fallback: use first edit if we couldn't find the specific one
            if failed_search_block is None and updates[failed_idx].edits:
                failed_search_block = updates[failed_idx].edits[0].search_block

        return {
            **state,
            "error": error_msg,
            "last_edit_error": error_msg,
            "failed_search_block": failed_search_block,
            "retry_file_index": failed_idx,
        }
