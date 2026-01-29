"""Summary cache management for two-stage context loading."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai import Agent

from ..prompts import SUMMARY_SYSTEM_PROMPT


class SummaryCache:
    """Manages cached document summaries with staleness detection."""

    def __init__(self, data_dir: Path):
        """Initialize the summary cache.

        Args:
            data_dir: Path to the data directory (e.g., 'data/')
        """
        self.data_dir = data_dir
        self.cache_dir = data_dir / ".laibrary"
        self.cache_file = self.cache_dir / "summaries.json"
        self._cache: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load summaries from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._cache = {}
        else:
            self._cache = {}

    def _save(self) -> None:
        """Save summaries to disk."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(self._cache, f, indent=2)

    @staticmethod
    def _hash_content(content: str) -> str:
        """Generate a hash for content to detect staleness."""
        return hashlib.md5(content.encode()).hexdigest()[:8]

    def get(self, file_path: str, content: str) -> str | None:
        """Get a cached summary if it exists and is fresh.

        Args:
            file_path: Relative path to the document
            content: Current content of the document

        Returns:
            Cached summary if fresh, None if stale or missing
        """
        entry = self._cache.get(file_path)
        if entry is None:
            return None

        # Check staleness via content hash
        current_hash = self._hash_content(content)
        if entry.get("hash") != current_hash:
            return None

        return entry.get("summary")

    def set(self, file_path: str, content: str, summary: str) -> None:
        """Store a summary in the cache.

        Args:
            file_path: Relative path to the document
            content: Current content of the document
            summary: Generated summary to cache
        """
        self._cache[file_path] = {
            "summary": summary,
            "hash": self._hash_content(content),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._save()

    def remove(self, file_path: str) -> None:
        """Remove a summary from the cache.

        Args:
            file_path: Relative path to the document to remove
        """
        if file_path in self._cache:
            del self._cache[file_path]
            self._save()

    def get_all_summaries(self) -> dict[str, str]:
        """Get all cached summaries (without staleness checking).

        Returns:
            Dict mapping file paths to their summaries
        """
        return {path: entry["summary"] for path, entry in self._cache.items()}


async def generate_summary(content: str) -> str:
    """Generate a summary for document content using an LLM.

    Args:
        content: Document content to summarize

    Returns:
        Generated summary (1-2 sentences)
    """
    from ..config import MAX_RETRIES, SUMMARY_SETTINGS

    agent = Agent(
        os.environ["MODEL"],
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        retries=MAX_RETRIES,
        model_settings=SUMMARY_SETTINGS,
    )

    result = await agent.run(f"Summarize this document:\n\n{content}")
    return result.output
