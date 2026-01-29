"""Laibrary - Evolutionary PKM System."""

from .chat import ChatMessage, ChatSession, MessageRole, run_chat_session
from .cli import app as cli_app
from .exceptions import EditApplicationError
from .git_wrapper import IsolatedGitRepo
from .schemas import DocumentEdit, DocumentUpdate, PKMState
from .workflow import create_workflow, run_workflow, run_workflow_with_state

# Universal settings
MAX_RETRIES = 3

__all__ = [
    "cli_app",
    "create_workflow",
    "run_workflow",
    "run_workflow_with_state",
    "run_chat_session",
    "ChatSession",
    "ChatMessage",
    "MessageRole",
    "IsolatedGitRepo",
    "DocumentEdit",
    "DocumentUpdate",
    "PKMState",
    "EditApplicationError",
    "MAX_RETRIES",
]
