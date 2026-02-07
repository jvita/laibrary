"""ChatSession class for managing chat interactions."""

from dataclasses import dataclass, field
from pathlib import Path

import logfire

from ..projects import list_projects, load_project, project_exists
from ..session_manager import SessionManager
from ..workflow import run_workflow_with_state
from .intent import (
    _build_chat_context,
    _create_router_agent,
    _handle_query,
    _is_query_intent,
)
from .models import ChatMessage, Intent, MessageRole


@dataclass
class ChatSession:
    """Manages a chat session with the PKM system."""

    data_dir: Path
    history: list[ChatMessage] = field(default_factory=list)
    current_project: str | None = None  # Currently selected project name
    session_manager: SessionManager | None = field(default=None)

    def __post_init__(self) -> None:
        """Initialize session manager."""
        if self.session_manager is None:
            self.session_manager = SessionManager(data_dir=self.data_dir)

    def _record_assistant_message(self, content: str) -> None:
        """Record an assistant message to the session transcript."""
        if self.session_manager:
            self.session_manager.record_message("assistant", content)

    @logfire.instrument("chat_message")
    async def send_message(self, user_message: str) -> dict:
        """Process a user message and return the response.

        Commands:
        - /list or /projects - List available projects
        - /use <project> - Set current project for session
        - /read [project] - Print project document (defaults to current)
        - /<project> <note> - Add note to specific project (and switch to it)
        - Plain text - Add note to current project (if set) or route via intent

        Returns:
            Dict with keys:
                - response: str - The assistant's response
                - updated_docs: bool - Whether documents were updated
                - update_details: dict | None - Details about the update if any
        """
        # Add user message to history and transcript
        self.history.append(ChatMessage(role=MessageRole.USER, content=user_message))
        if self.session_manager:
            self.session_manager.record_message("user", user_message)

        stripped = user_message.strip()
        lower = stripped.lower()

        # Command: /list or /projects
        if lower in ("/list", "/projects"):
            projects = list_projects(self.data_dir)
            if projects:
                response = "**Available projects:**\n" + "\n".join(
                    f"- {p}" for p in projects
                )
                if self.current_project:
                    response += f"\n\n*Current: {self.current_project}*"
            else:
                response = "No projects yet. Use `/use project-name` to create one."
            self.history.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=response)
            )
            self._record_assistant_message(response)
            return {
                "response": response,
                "updated_docs": False,
                "update_details": None,
            }

        # Command: /use <project>
        if lower.startswith("/use "):
            project_name = stripped[5:].strip()
            if not project_name:
                response = "Usage: /use project-name"
            else:
                self.current_project = project_name
                if project_exists(self.data_dir, project_name):
                    response = f"Switched to project: **{project_name}**"
                else:
                    response = f"Set project to: **{project_name}** (will be created on first note)"
            self.history.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=response)
            )
            self._record_assistant_message(response)
            return {
                "response": response,
                "updated_docs": False,
                "update_details": None,
            }

        # Command: /read [project] - print project document
        if lower == "/read" or lower.startswith("/read "):
            if lower == "/read":
                project_name = self.current_project
                if not project_name:
                    response = "No project selected. Use `/read project-name` or `/use project-name` first."
                    self.history.append(
                        ChatMessage(role=MessageRole.ASSISTANT, content=response)
                    )
                    self._record_assistant_message(response)
                    return {
                        "response": response,
                        "updated_docs": False,
                        "update_details": None,
                    }
            else:
                project_name = stripped[6:].strip()
                if not project_name:
                    response = "Usage: `/read project-name`"
                    self.history.append(
                        ChatMessage(role=MessageRole.ASSISTANT, content=response)
                    )
                    self._record_assistant_message(response)
                    return {
                        "response": response,
                        "updated_docs": False,
                        "update_details": None,
                    }

            content = load_project(self.data_dir, project_name)
            if content is None:
                response = f"Project **{project_name}** not found."
            else:
                response = content

            self.history.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=response)
            )
            self._record_assistant_message(response)
            return {
                "response": response,
                "updated_docs": False,
                "update_details": None,
            }

        # Command: /<project> <note> - explicit project with note
        if stripped.startswith("/") and " " in stripped:
            # Parse /project-name note content
            parts = stripped[1:].split(" ", 1)
            project_name = parts[0]
            note_content = parts[1] if len(parts) > 1 else ""

            if note_content:
                # Switch to this project and add note
                self.current_project = project_name
                return await self._add_note(note_content)

        # Command: /<project> (no note) - just switch project
        if stripped.startswith("/") and " " not in stripped:
            project_name = stripped[1:]
            if project_name:
                self.current_project = project_name
                if project_exists(self.data_dir, project_name):
                    response = f"Switched to project: **{project_name}**"
                else:
                    response = f"Set project to: **{project_name}** (will be created on first note)"
                self.history.append(
                    ChatMessage(role=MessageRole.ASSISTANT, content=response)
                )
                self._record_assistant_message(response)
                return {
                    "response": response,
                    "updated_docs": False,
                    "update_details": None,
                }

        # If we have a current project, check if it's a query first
        if self.current_project:
            if _is_query_intent(stripped):
                # Handle as query with project context
                return await self._handle_query_with_project(stripped)
            else:
                # Handle as note/update
                return await self._add_note(stripped)

        # No current project - route through intent classifier
        return await self._route_message(stripped)

    async def _add_note(self, note_content: str) -> dict:
        """Add a note to the current project."""
        if not self.current_project:
            response = "No project selected. Use `/use project-name` first."
            self.history.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=response)
            )
            self._record_assistant_message(response)
            return {
                "response": response,
                "updated_docs": False,
                "update_details": None,
            }

        user_input = f"/{self.current_project} {note_content}"
        initial_state = {
            "user_input": user_input,
            "confirmation_mode": "auto",  # Auto-confirm in chat mode
        }
        workflow_result = await run_workflow_with_state(initial_state, self.data_dir)

        if workflow_result.get("error"):
            error = workflow_result["error"]
            logfire.error("Workflow failed", error=error)
            response = f"Error: {error}"
            update_details = None
        elif workflow_result.get("committed"):
            update = workflow_result.get("document_update")
            if update:
                update_details = {
                    "file": update.target_file,
                    "commit_message": update.commit_message,
                }
                response = f"Updated **{self.current_project}**"
                # Record project touch for session document
                if self.session_manager and self.current_project:
                    self.session_manager.record_project_touch(self.current_project)
            else:
                response = "Changes committed."
                update_details = None
        else:
            response = "No changes were made."
            update_details = None

        self.history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response))
        self._record_assistant_message(response)
        return {
            "response": response,
            "updated_docs": workflow_result.get("committed", False),
            "update_details": update_details,
        }

    async def _handle_query_with_project(self, message: str) -> dict:
        """Handle a query when a project is selected."""
        target_hint = f"{self.current_project} project"

        try:
            answer = await _handle_query(message, self.data_dir, target_hint)
            response = answer
        except Exception as e:
            logfire.error("Query handling failed", error=str(e))
            response = f"I encountered an error: {e}"

        self.history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response))
        self._record_assistant_message(response)
        return {
            "response": response,
            "updated_docs": False,
            "update_details": None,
        }

    async def _route_message(self, message: str) -> dict:
        """Route a message through intent classification."""
        context = _build_chat_context(self.history[:-1])
        prompt = f"{context}\n\n## Current Message\n{message}"

        router = _create_router_agent()

        try:
            result = await router.run(prompt)
            decision = result.output
        except Exception as e:
            logfire.error("Router failed", error=str(e))
            error_response = f"I encountered an error processing your message: {e}"
            self.history.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=error_response)
            )
            self._record_assistant_message(error_response)
            return {
                "response": error_response,
                "updated_docs": False,
                "update_details": None,
            }

        logfire.info(
            "Router decision",
            intent=decision.intent,
            reasoning=decision.reasoning,
        )

        update_details = None

        match decision.intent:
            case Intent.CHAT:
                response = decision.response

            case Intent.QUERY:
                try:
                    answer = await _handle_query(
                        message, self.data_dir, decision.target_hint
                    )
                    response = answer
                except Exception as e:
                    logfire.error("Query handling failed", error=str(e))
                    response = f"I encountered an error searching your documents: {e}"

            case Intent.UPDATE:
                # Guide user to select a project first
                projects = list_projects(self.data_dir)
                if projects:
                    project_list = ", ".join(projects)
                    response = f"{decision.response}\n\nTo save this, first select a project:\n`/use project-name`\n\nAvailable: {project_list}"
                else:
                    response = f"{decision.response}\n\nTo save this, first create a project:\n`/use project-name`"

        self.history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response))
        self._record_assistant_message(response)
        return {
            "response": response,
            "updated_docs": False,
            "update_details": update_details,
        }

    def clear_history(self) -> None:
        """Clear the chat history."""
        self.history.clear()

    async def end_session(self) -> str | None:
        """End the current session and persist to disk.

        Returns:
            Path to the session file, or None if session was empty.
        """
        if self.session_manager:
            return await self.session_manager.end_session()
        return None
