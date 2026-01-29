"""Context node - gathers existing documents for the Architect."""

from pathlib import Path

from ..git_wrapper import IsolatedGitRepo
from ..schemas import PKMState


def context_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Scan data/ directory and read markdown files.

    Provides the Architect with context of existing documents.
    Respects selected_files from the selector stage:
    - If selected_files is a list: load only those files
    - If selected_files is None: load all files (original behavior)
    """
    if state.get("error"):
        return state

    if data_dir is None:
        data_dir = Path("data")

    repo = IsolatedGitRepo(data_dir)

    context_files: dict[str, str] = {}
    selected_files = state.get("selected_files")

    if selected_files is not None:
        # Load only selected files
        for file_path in selected_files:
            content = repo.get_file_content(file_path)
            if content is not None:
                context_files[file_path] = content
    else:
        # Load all markdown files (original behavior)
        for file_path in repo.list_files("**/*.md"):
            content = repo.get_file_content(file_path)
            if content is not None:
                context_files[file_path] = content

    return {**state, "context_files": context_files}
