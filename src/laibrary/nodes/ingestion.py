"""Ingestion node - validates input and parses project commands."""

import re
from pathlib import Path

from ..git_wrapper import IsolatedGitRepo
from ..projects import list_projects
from ..schemas import PKMState


def ingestion_node(state: PKMState, data_dir: Path | None = None) -> PKMState:
    """Validate user input and parse project commands.

    Syntax:
    - /list or /projects - List available projects
    - /<project-name> <note> - Add note to specified project (or create it)
    - Anything else - Error (must specify project)

    In auto-confirm mode (bulk import), new projects are created automatically.
    In interactive mode, new projects are only created if no other projects exist.

    Args:
        state: Current workflow state
        data_dir: Path to data directory

    Returns:
        Updated state with parsed command info
    """
    if data_dir is None:
        data_dir = Path("data")

    user_input = state.get("user_input", "")
    confirmation_mode = state.get("confirmation_mode", "interactive")

    if not user_input:
        return {**state, "error": "No user input provided"}

    cleaned = user_input.strip()

    if not cleaned:
        return {**state, "error": "User input is empty after stripping whitespace"}

    # Check for /list or /projects command
    if cleaned.lower() in ("/list", "/projects"):
        return {**state, "command": "list", "user_input": cleaned}

    # Check for /project-name pattern
    match = re.match(r"^/([a-zA-Z0-9_-]+)\s*(.*)", cleaned, re.DOTALL)

    if not match:
        # No project specified
        projects = list_projects(data_dir)
        if projects:
            project_list = ", ".join(projects)
            return {
                **state,
                "error": f"Please specify a project with /project-name. Available projects: {project_list}",
            }
        else:
            return {
                **state,
                "error": "Please specify a project with /project-name. No projects exist yet - the first note will create one.",
            }

    project_name = match.group(1)
    note_content = match.group(2).strip()

    # Build project file path
    target_project = f"projects/{project_name}.md"

    # Check if project exists
    repo = IsolatedGitRepo(data_dir)
    if not repo.file_exists(target_project):
        # In auto-confirm mode (bulk import), allow creating new projects
        if confirmation_mode == "auto":
            pass  # Allow creation
        else:
            # Interactive mode - only allow creation if no other projects exist
            projects = list_projects(data_dir)
            if projects:
                project_list = ", ".join(projects)
                return {
                    **state,
                    "error": f"Project '{project_name}' does not exist. Available projects: {project_list}",
                }
            # No projects exist - allow creation

    if not note_content:
        return {
            **state,
            "error": f"Please provide a note after /{project_name}. Example: /{project_name} added new feature",
        }

    return {
        **state,
        "command": "note",
        "target_project": target_project,
        "note_content": note_content,
        "user_input": cleaned,
    }
