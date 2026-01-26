"""Git-based version control for summaries."""

from datetime import datetime
from pathlib import Path
from uuid import UUID

from git import Repo
from git.exc import InvalidGitRepositoryError

from .graph_models import Summary, VersionInfo


class SummaryVersionControl:
    """Manage git-based version control for summaries."""

    def __init__(self, repo_path: Path):
        """Initialize version control.

        Args:
            repo_path: Path to the git repository root
        """
        try:
            self.repo = Repo(repo_path, search_parent_directories=True)
        except InvalidGitRepositoryError:
            # Initialize a new repo if none exists
            self.repo = Repo.init(repo_path)

    def commit_summary(
        self,
        summary: Summary,
        message: str | None = None,
    ) -> str:
        """Commit a summary update.

        Args:
            summary: The summary that was updated
            message: Optional commit message (auto-generated if not provided)

        Returns:
            The commit hash
        """
        # Add the summary file
        rel_path = summary.path.relative_to(self.repo.working_dir)
        self.repo.index.add([str(rel_path)])

        # Generate commit message if not provided
        if message is None:
            if summary.version == 1:
                message = f"[laibrary] Create summary: {summary.topic}"
            else:
                message = (
                    f"[laibrary] Update summary: {summary.topic} (v{summary.version})"
                )

        # Add metadata to commit message
        message += f"\n\nIncorporated notes: {len(summary.incorporated_note_ids)}"

        # Commit
        commit = self.repo.index.commit(message)
        return commit.hexsha

    def get_history(self, summary_path: Path) -> list[VersionInfo]:
        """Get version history for a summary.

        Args:
            summary_path: Path to the summary file

        Returns:
            List of VersionInfo objects, most recent first
        """
        try:
            rel_path = summary_path.relative_to(self.repo.working_dir)
        except ValueError:
            # Path is not relative to repo
            return []

        versions = []
        version_num = 0

        # Iterate through commits that touched this file
        for commit in self.repo.iter_commits(paths=str(rel_path)):
            version_num += 1
            # Parse incorporated note count from commit message if present
            note_ids: list[UUID] = []  # Would need metadata file to track actual IDs

            versions.append(
                VersionInfo(
                    version=version_num,
                    git_commit=commit.hexsha,
                    created_at=datetime.fromtimestamp(commit.committed_date),
                    incorporated_note_ids=note_ids,
                )
            )

        # Reverse to get chronological order with correct version numbers
        for i, v in enumerate(reversed(versions)):
            v.version = i + 1

        return list(reversed(versions))

    def diff_versions(
        self,
        summary_path: Path,
        commit1: str,
        commit2: str,
    ) -> str:
        """Show diff between two versions.

        Args:
            summary_path: Path to the summary file
            commit1: First commit hash
            commit2: Second commit hash

        Returns:
            Diff string
        """
        try:
            rel_path = summary_path.relative_to(self.repo.working_dir)
        except ValueError:
            return ""

        return self.repo.git.diff(commit1, commit2, "--", str(rel_path))

    def get_version_content(self, summary_path: Path, commit: str) -> str | None:
        """Get the content of a summary at a specific version.

        Args:
            summary_path: Path to the summary file
            commit: Commit hash

        Returns:
            File content at that version, or None if not found
        """
        try:
            rel_path = summary_path.relative_to(self.repo.working_dir)
            blob = self.repo.commit(commit).tree / str(rel_path)
            return blob.data_stream.read().decode("utf-8")
        except (KeyError, ValueError):
            return None


def commit_summary_update(
    summary: Summary,
    repo_path: Path,
    message: str | None = None,
) -> str:
    """Convenience function to commit a summary update.

    Args:
        summary: The summary that was updated
        repo_path: Path to the git repository
        message: Optional commit message

    Returns:
        The commit hash
    """
    vc = SummaryVersionControl(repo_path)
    return vc.commit_summary(summary, message)


def get_summary_history(
    summary_path: Path,
    repo_path: Path,
) -> list[VersionInfo]:
    """Convenience function to get summary history.

    Args:
        summary_path: Path to the summary file
        repo_path: Path to the git repository

    Returns:
        List of VersionInfo objects
    """
    vc = SummaryVersionControl(repo_path)
    return vc.get_history(summary_path)
