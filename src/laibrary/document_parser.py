"""Document parser for section-based editing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import SectionEdit

# Canonical section order
SECTION_ORDER = [
    "Description",
    "Current Status",
    "To Do",
    "Brainstorming",
    "Summary",
    "Notes",
]


def parse_document(content: str) -> tuple[str, dict[str, str]]:
    """Extract title and sections from a markdown document.

    Args:
        content: Full markdown document content

    Returns:
        Tuple of (title, sections_dict) where sections_dict maps
        section names to their content (without the ## header)
    """
    lines = content.split("\n")
    title = ""
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_content: list[str] = []

    for line in lines:
        # Check for title (# heading)
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue

        # Check for section header (## heading)
        if line.startswith("## "):
            # Save previous section if any
            if current_section is not None:
                sections[current_section] = "\n".join(current_content).strip()

            current_section = line[3:].strip()
            current_content = []
            continue

        # Accumulate content for current section
        if current_section is not None:
            current_content.append(line)

    # Save final section
    if current_section is not None:
        sections[current_section] = "\n".join(current_content).strip()

    return title, sections


def apply_edits(sections: dict[str, str], edits: list[SectionEdit]) -> dict[str, str]:
    """Apply section edits to a sections dictionary.

    Args:
        sections: Current sections dictionary
        edits: List of SectionEdit objects to apply

    Returns:
        Updated sections dictionary
    """
    result = dict(sections)

    for edit in edits:
        if edit.remove:
            # Remove the section if it exists
            result.pop(edit.section, None)
        else:
            # Update or add the section
            result[edit.section] = edit.content

    return result


def render_document(title: str, sections: dict[str, str]) -> str:
    """Reassemble a markdown document from title and sections.

    Sections are rendered in canonical order (Description, Current Status,
    To Do, Notes), followed by any unknown sections in alphabetical order.

    Args:
        title: Document title (without # prefix)
        sections: Dictionary mapping section names to content

    Returns:
        Complete markdown document as a string
    """
    parts = [f"# {title}"]

    # Track which sections we've rendered
    rendered = set()

    # Render canonical sections in order
    for section_name in SECTION_ORDER:
        if section_name in sections:
            parts.append("")
            parts.append(f"## {section_name}")
            content = sections[section_name]
            if content:
                parts.append(content)
            rendered.add(section_name)

    # Render any unknown sections in alphabetical order
    unknown_sections = sorted(set(sections.keys()) - rendered)
    for section_name in unknown_sections:
        parts.append("")
        parts.append(f"## {section_name}")
        content = sections[section_name]
        if content:
            parts.append(content)

    # Ensure single trailing newline
    return "\n".join(parts) + "\n"


def create_default_document(title: str, description: str = "") -> str:
    """Create a new document with the default template.

    Args:
        title: Project title
        description: Optional initial description

    Returns:
        Markdown document with default structure
    """
    sections = {}
    if description:
        sections["Description"] = description

    return render_document(title, sections)
