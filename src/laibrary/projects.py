"""Project utilities for listing, loading, and checking projects."""

from pathlib import Path

from .git_wrapper import IsolatedGitRepo


def list_projects(data_dir: Path) -> list[str]:
    """List available project names (without .md extension)."""
    repo = IsolatedGitRepo(data_dir)
    projects = []
    for file_path in repo.list_files("projects/*.md"):
        name = file_path.replace("projects/", "").replace(".md", "")
        projects.append(name)
    return sorted(projects)


def load_project(data_dir: Path, project_name: str) -> str | None:
    """Load a project document by name."""
    repo = IsolatedGitRepo(data_dir)
    file_path = f"projects/{project_name}.md"
    return repo.get_file_content(file_path)


def project_exists(data_dir: Path, project_name: str) -> bool:
    """Check if a project exists."""
    repo = IsolatedGitRepo(data_dir)
    return repo.file_exists(f"projects/{project_name}.md")
