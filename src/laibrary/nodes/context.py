"""Context node - gathers existing documents for the Architect."""

from pathlib import Path

from ..git_wrapper import IsolatedGitRepo
from ..schemas import PKMState


def context_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Scan data/ directory and read all markdown files.

    Provides the Architect with full context of existing documents.
    """
    if state.get("error"):
        return state

    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)

    context_files: dict[str, str] = {}

    for file_path in repo.list_files("**/*.md"):
        content = repo.get_file_content(file_path)
        if content is not None:
            context_files[file_path] = content

    return {**state, "context_files": context_files}
