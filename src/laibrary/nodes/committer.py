"""Committer node - applies section edits and commits."""

import re
from pathlib import Path

import logfire

from ..document_parser import (
    apply_edits,
    parse_document,
    render_document,
)
from ..git_wrapper import IsolatedGitRepo
from ..schemas import PKMState, SectionEdit


def _sanitize_section_content(section_name: str, content: str) -> str:
    """Remove section headers that the LLM might have included."""
    # Pattern: ## Section Name at start of content
    header_pattern = rf"^\s*##\s+{re.escape(section_name)}\s*\n"

    # Remove the header if present
    cleaned = re.sub(header_pattern, "", content, count=1, flags=re.MULTILINE)

    return cleaned.strip()


def _format_date_logs(notes_content: str) -> str:
    """Ensure Notes section has proper date log format with reverse chronological order."""
    from datetime import date

    today = date.today().strftime("%Y-%m-%d")

    # Pattern to find date headers: ### YYYY-MM-DD
    date_pattern = r"^###\s+(\d{4}-\d{2}-\d{2})\s*$"

    lines = notes_content.split("\n")
    date_sections = {}  # {date: [content_lines]}
    current_date = None
    current_lines = []
    undated_lines = []

    for line in lines:
        match = re.match(date_pattern, line.strip())
        if match:
            # Save previous section
            if current_date:
                date_sections[current_date] = current_lines
            elif current_lines:
                undated_lines = current_lines

            # Start new section
            current_date = match.group(1)
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    if current_date:
        date_sections[current_date] = current_lines
    elif current_lines:
        undated_lines = current_lines

    # Assign undated content to today
    if undated_lines:
        if today in date_sections:
            date_sections[today] = undated_lines + ["\n"] + date_sections[today]
        else:
            date_sections[today] = undated_lines

    # Sort dates in reverse chronological order
    sorted_dates = sorted(date_sections.keys(), reverse=True)

    # Rebuild content
    rebuilt = []
    for date_str in sorted_dates:
        rebuilt.append(f"### {date_str}")
        content = date_sections[date_str]
        # Clean up empty lines
        while content and not content[0].strip():
            content.pop(0)
        while content and not content[-1].strip():
            content.pop()
        rebuilt.extend(content)
        rebuilt.append("")  # Blank line between sections

    return "\n".join(rebuilt).strip()


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

        # Apply section edits with sanitization
        sanitized_edits = []
        for edit in update.section_edits:
            if not edit.remove:
                # Sanitize content to remove duplicate headers
                clean_content = _sanitize_section_content(edit.section, edit.content)
                sanitized_edits.append(
                    SectionEdit(
                        section=edit.section, content=clean_content, remove=edit.remove
                    )
                )
            else:
                sanitized_edits.append(edit)

        updated_sections = apply_edits(sections, sanitized_edits)

        # Apply date log formatting to Notes section
        if "Notes" in updated_sections:
            updated_sections["Notes"] = _format_date_logs(updated_sections["Notes"])

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
