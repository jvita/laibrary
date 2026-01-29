"""Simple markdown file parser for bulk import."""

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParsedNote:
    """A parsed note from a markdown file."""

    path: Path
    title: str
    content: str
    content_hash: str


def parse_markdown_directory(directory: Path) -> list[ParsedNote]:
    """Parse all markdown files in a directory.

    Args:
        directory: Path to directory containing .md files

    Returns:
        List of parsed notes
    """
    notes = []

    for md_file in sorted(directory.rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")

        # Extract title from first H1 or filename
        title = _extract_title(content, md_file.stem)

        # Hash for deduplication
        content_hash = hashlib.md5(content.encode()).hexdigest()

        notes.append(
            ParsedNote(
                path=md_file,
                title=title,
                content=content,
                content_hash=content_hash,
            )
        )

    return notes


def _extract_title(content: str, fallback: str) -> str:
    """Extract title from first H1 heading or use fallback."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def deduplicate(
    notes: list[ParsedNote],
) -> tuple[list[ParsedNote], list[ParsedNote]]:
    """Remove exact duplicates based on content hash.

    Returns:
        (unique_notes, duplicates)
    """
    seen_hashes: set[str] = set()
    unique: list[ParsedNote] = []
    duplicates: list[ParsedNote] = []

    for note in notes:
        if note.content_hash in seen_hashes:
            duplicates.append(note)
        else:
            seen_hashes.add(note.content_hash)
            unique.append(note)

    return unique, duplicates
