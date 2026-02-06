"""Context node - loads the target project document."""

from pathlib import Path

from ..git_wrapper import IsolatedGitRepo
from ..schemas import PKMState


def context_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Load the target project document into context.

    Args:
        state: Current workflow state (must have target_project set)
        data_dir: Path to data directory

    Returns:
        Updated state with context_files containing the project document
    """
    if state.get("error"):
        return state

    if data_dir is None:
        data_dir = Path("data")

    target_project = state.get("target_project")
    if not target_project:
        return {**state, "error": "No target project specified"}

    repo = IsolatedGitRepo(data_dir)
    context_files: dict[str, str] = {}

    # Load the target project file
    content = repo.get_file_content(target_project)

    if content is not None:
        # # Strip the "## Session History" section, which is always last.
        # # This helps to avoid polluting the context window.
        # split_marker = "## Session History"
        # if split_marker in content:
        #     content = content.split(split_marker)[0].strip()

        context_files[target_project] = content
    # If file doesn't exist, that's OK - architect will create it

    return {**state, "context_files": context_files}
