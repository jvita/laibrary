"""Session manager for tracking chat sessions as first-class notes."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic_ai import Agent

from .config import MAX_RETRIES, QUERY_SETTINGS
from .git_wrapper import IsolatedGitRepo


@dataclass
class TranscriptEntry:
    """A single entry in the session transcript."""

    timestamp: datetime
    role: str  # "user" or "assistant"
    content: str


@dataclass
class SessionManager:
    """Manages chat session lifecycle and persistence.

    Sessions are stored as markdown files at data/sessions/YYYY-MM-DD_HH-MM-SS.md
    with bidirectional links to projects touched during the session.
    """

    data_dir: Path
    session_id: str = field(default="")
    started_at: datetime = field(default_factory=datetime.now)
    projects_touched: set[str] = field(default_factory=set)
    transcript: list[TranscriptEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Start a new session on initialization."""
        if not self.session_id:
            self.start_session()

    def start_session(self) -> str:
        """Start a new session and return the session ID.

        Session ID format: YYYY-MM-DD_HH-MM-SS
        """
        self.started_at = datetime.now()
        self.session_id = self.started_at.strftime("%Y-%m-%d_%H-%M-%S")
        self.projects_touched = set()
        self.transcript = []
        return self.session_id

    def record_message(self, role: str, content: str) -> None:
        """Record a message in the transcript.

        Args:
            role: "user" or "assistant"
            content: The message content
        """
        self.transcript.append(
            TranscriptEntry(
                timestamp=datetime.now(),
                role=role,
                content=content,
            )
        )

    def record_project_touch(self, project_name: str) -> None:
        """Record that a project was touched during this session.

        Args:
            project_name: Name of the project (without path or extension)
        """
        self.projects_touched.add(project_name)

    def has_content(self) -> bool:
        """Check if the session has any meaningful content to persist.

        Returns:
            True if there are user or assistant messages in the transcript.
        """
        return any(entry.role in ("user", "assistant") for entry in self.transcript)

    async def end_session(self) -> str | None:
        """End the session and persist to disk.

        Generates a summary using LLM, writes the session document,
        and commits to git.

        Returns:
            Path to the created session file, or None if session was empty.
        """
        if not self.has_content():
            return None

        ended_at = datetime.now()

        # Generate summary
        summary = await self._generate_summary()

        # Format the session document
        doc_content = self._format_session_document(ended_at, summary)

        # Write to disk and commit
        session_path = self._write_session_document(doc_content)

        # Start a new session
        self.start_session()

        return session_path

    async def _generate_summary(self) -> str:
        """Generate a 2-3 sentence summary of the conversation using LLM."""
        if not self.transcript:
            return "Empty session."

        # Build transcript text for summarization
        transcript_text = self._format_transcript_for_summary()

        prompt = f"""Summarize this conversation in 2-3 sentences. Focus on what was discussed and any key outcomes.

{transcript_text}"""

        try:
            agent = Agent(
                os.environ["MODEL"],
                system_prompt="You are a helpful assistant that summarizes conversations concisely.",
                retries=MAX_RETRIES,
                model_settings=QUERY_SETTINGS,
            )
            result = await agent.run(prompt)
            return result.output
        except Exception:
            # Fallback if summarization fails
            return "Session summary unavailable."

    def _format_transcript_for_summary(self) -> str:
        """Format transcript entries for summarization."""
        parts = []
        for entry in self.transcript:
            role_label = "User" if entry.role == "user" else "Assistant"
            parts.append(f"{role_label}: {entry.content}")
        return "\n\n".join(parts)

    def _format_session_document(self, ended_at: datetime, summary: str) -> str:
        """Format the complete session document in markdown.

        Format:
        # Chat Session - YYYY-MM-DD HH:MM

        ## Metadata
        - Projects: [[project-a]], [[project-b]]
        - Messages: 12
        - Started: 14:30
        - Ended: 14:55

        ## Summary
        (LLM-generated summary)

        ## Transcript
        ### 14:30 - User
        Message content...
        """
        date_str = self.started_at.strftime("%Y-%m-%d %H:%M")
        started_time = self.started_at.strftime("%H:%M")
        ended_time = ended_at.strftime("%H:%M")

        # Format project links
        if self.projects_touched:
            projects_str = ", ".join(f"[[{p}]]" for p in sorted(self.projects_touched))
        else:
            projects_str = "(none)"

        # Count messages (user + assistant only)
        message_count = sum(
            1 for e in self.transcript if e.role in ("user", "assistant")
        )

        # Build document
        parts = [
            f"# Chat Session - {date_str}",
            "",
            "## Metadata",
            f"- Projects: {projects_str}",
            f"- Messages: {message_count}",
            f"- Started: {started_time}",
            f"- Ended: {ended_time}",
            "",
            "## Summary",
            summary,
            "",
            "## Transcript",
        ]

        # Add transcript entries
        for entry in self.transcript:
            time_str = entry.timestamp.strftime("%H:%M")
            role_label = "User" if entry.role == "user" else "Assistant"
            parts.append(f"### {time_str} - {role_label}")
            parts.append(entry.content)
            parts.append("")

        return "\n".join(parts)

    def _write_session_document(self, content: str) -> str:
        """Write the session document to disk and commit.

        Args:
            content: The formatted markdown content

        Returns:
            Relative path to the session file
        """
        repo = IsolatedGitRepo(self.data_dir)

        # Ensure sessions directory exists
        sessions_dir = self.data_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Write file
        file_path = f"sessions/{self.session_id}.md"
        repo.write_file(file_path, content)

        # Commit
        commit_msg = f"session: Add chat session {self.session_id}"
        repo.add_and_commit(file_path, commit_msg)

        return file_path

    def get_current_session_id(self) -> str:
        """Get the current session ID."""
        return self.session_id
