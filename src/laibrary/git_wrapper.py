"""Isolated git operations for the PKM data directory."""

from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError


class IsolatedGitRepo:
    """Git repository isolated to the data/ directory.

    Uses separate --git-dir and --work-tree to keep the PKM git history
    separate from any parent repository.
    """

    def __init__(self, data_dir: Path):
        """Initialize the class."""
        self.data_dir = data_dir
        self.git_dir = data_dir / ".git"

    def init(self) -> Repo:
        """Initialize a new git repository in the data directory."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if self.git_dir.exists():
            return Repo(self.git_dir, search_parent_directories=False)

        # Initialize with separate git-dir and work-tree
        repo = Repo.init(self.data_dir)
        return repo

    def _get_repo(self) -> Repo:
        """Get the existing repository."""
        try:
            return Repo(self.data_dir, search_parent_directories=False)
        except InvalidGitRepositoryError as err:
            raise RuntimeError from err(
                f"No git repository found at {self.data_dir}. Run 'laibrary init' first."
            )

    def add_and_commit(self, file_path: str, message: str) -> str:
        """Stage a file and commit it.

        Args:
            file_path: Path relative to data_dir
            message: Commit message

        Returns:
            The commit SHA
        """
        repo = self._get_repo()
        repo.index.add([file_path])
        commit = repo.index.commit(message)
        return commit.hexsha

    def list_files(self, pattern: str = "**/*.md") -> list[str]:
        """List files matching a glob pattern in the data directory.

        Returns paths relative to data_dir.
        """
        files = []
        for path in self.data_dir.glob(pattern):
            if ".git" not in path.parts:
                files.append(str(path.relative_to(self.data_dir)))
        return sorted(files)

    def get_file_content(self, file_path: str) -> str | None:
        """Read content of a file in the data directory.

        Args:
            file_path: Path relative to data_dir

        Returns:
            File content or None if file doesn't exist
        """
        full_path = self.data_dir / file_path
        if not full_path.exists():
            return None
        return full_path.read_text()

    def write_file(self, file_path: str, content: str) -> None:
        """Write content to a file in the data directory.

        Creates parent directories if needed.

        Args:
            file_path: Path relative to data_dir
            content: Content to write
        """
        full_path = self.data_dir / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

    def file_exists(self, file_path: str) -> bool:
        """Check if a file exists in the data directory."""
        return (self.data_dir / file_path).exists()
