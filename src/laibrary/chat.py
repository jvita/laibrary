"""Chat interface for the PKM system."""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .config import MAX_RETRIES, QUERY_SETTINGS, ROUTER_SETTINGS
from .git_wrapper import IsolatedGitRepo
from .prompts import QUERY_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT
from .workflow import run_workflow_with_state


class Intent(str, Enum):
    """Type of user intent."""

    UPDATE = "update"  # Any document modification (add, remove, cleanup, reorganize)
    QUERY = "query"  # Read/retrieve from existing docs
    CHAT = "chat"  # Just conversation, no doc action


class MessageRole(str, Enum):
    """Role of a message in the chat."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ChatMessage:
    """A single message in the chat history."""

    role: MessageRole
    content: str


class RouterDecision(BaseModel):
    """Decision from the router agent about how to handle a user message."""

    intent: Intent = Field(
        description="The type of intent: UPDATE for document modifications, QUERY for reading/retrieving info, CHAT for conversation"
    )
    reasoning: str = Field(
        description="Brief explanation of why this decision was made"
    )
    response: str = Field(
        description="Conversational response to the user (used directly for CHAT intent)"
    )
    target_hint: str | None = Field(
        default=None,
        description="Natural language hint about which document(s) to target, e.g. 'PKM project' or 'to-do list'",
    )


def _create_router_agent() -> Agent[None, RouterDecision]:
    """Create the router agent that decides how to handle messages."""
    return Agent(
        os.environ["MODEL"],
        system_prompt=ROUTER_SYSTEM_PROMPT,
        output_type=RouterDecision,
        retries=MAX_RETRIES,
        model_settings=ROUTER_SETTINGS,
    )


def _build_chat_context(history: list[ChatMessage], max_messages: int = 10) -> str:
    """Build context string from recent chat history."""
    recent = history[-max_messages:] if len(history) > max_messages else history

    if not recent:
        return ""

    parts = ["## Recent Conversation\n"]
    for msg in recent:
        role_label = "User" if msg.role == MessageRole.USER else "Assistant"
        parts.append(f"**{role_label}:** {msg.content}\n")

    return "\n".join(parts)


def _list_projects(data_dir: Path) -> list[str]:
    """List available project names."""
    repo = IsolatedGitRepo(data_dir)
    projects = []
    for file_path in repo.list_files("projects/*.md"):
        name = file_path.replace("projects/", "").replace(".md", "")
        projects.append(name)
    return sorted(projects)


def _load_project(data_dir: Path, project_name: str) -> str | None:
    """Load a project document by name."""
    repo = IsolatedGitRepo(data_dir)
    file_path = f"projects/{project_name}.md"
    return repo.get_file_content(file_path)


async def _handle_query(
    user_message: str, data_dir: Path, target_hint: str | None = None
) -> str:
    """Handle a query intent by searching documents and answering the question.

    Args:
        user_message: The user's question
        data_dir: Path to the data directory
        target_hint: Optional hint about which documents to focus on

    Returns:
        Natural language answer to the question
    """
    repo = IsolatedGitRepo(data_dir)

    # Load all project documents for context
    documents: dict[str, str] = {}
    for file_path in repo.list_files("projects/*.md"):
        content = repo.get_file_content(file_path)
        if content is not None:
            documents[file_path] = content

    if not documents:
        return "I don't have any project documents yet. Create one with /use project-name and add some notes!"

    # Build context from documents
    doc_context_parts = ["# Your Knowledge Base\n"]
    for file_path, content in documents.items():
        doc_context_parts.append(f"\n## Document: {file_path}\n\n{content}\n")
    doc_context = "\n".join(doc_context_parts)

    # Build the query prompt
    query_parts = [doc_context, "\n---\n"]
    if target_hint:
        query_parts.append(
            f"\nNote: The user mentioned '{target_hint}' - focus on relevant documents.\n"
        )
    query_parts.append(f"\n## User Question\n{user_message}")

    query_prompt = "".join(query_parts)

    # Create query agent and get answer
    query_agent = Agent(
        os.environ["MODEL"],
        system_prompt=QUERY_SYSTEM_PROMPT,
        retries=MAX_RETRIES,
        model_settings=QUERY_SETTINGS,
    )

    try:
        result = await query_agent.run(query_prompt)
        return result.output
    except Exception as e:
        logfire.error("Query agent failed", error=str(e))
        return f"I encountered an error searching your documents: {e}"


@dataclass
class ChatSession:
    """Manages a chat session with the PKM system."""

    data_dir: Path
    history: list[ChatMessage] = field(default_factory=list)
    current_project: str | None = None  # Currently selected project name

    def _project_exists(self, project_name: str) -> bool:
        """Check if a project exists."""
        repo = IsolatedGitRepo(self.data_dir)
        return repo.file_exists(f"projects/{project_name}.md")

    @logfire.instrument("chat_message")
    async def send_message(self, user_message: str) -> dict:
        """Process a user message and return the response.

        Commands:
        - /list or /projects - List available projects
        - /use <project> - Set current project for session
        - /<project> <note> - Add note to specific project (and switch to it)
        - Plain text - Add note to current project (if set) or route via intent

        Returns:
            Dict with keys:
                - response: str - The assistant's response
                - updated_docs: bool - Whether documents were updated
                - update_details: dict | None - Details about the update if any
        """
        # Add user message to history
        self.history.append(ChatMessage(role=MessageRole.USER, content=user_message))

        stripped = user_message.strip()
        lower = stripped.lower()

        # Command: /list or /projects
        if lower in ("/list", "/projects"):
            projects = _list_projects(self.data_dir)
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
                if self._project_exists(project_name):
                    response = f"Switched to project: **{project_name}**"
                else:
                    response = f"Set project to: **{project_name}** (will be created on first note)"
            self.history.append(
                ChatMessage(role=MessageRole.ASSISTANT, content=response)
            )
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
                if self._project_exists(project_name):
                    response = f"Switched to project: **{project_name}**"
                else:
                    response = f"Set project to: **{project_name}** (will be created on first note)"
                self.history.append(
                    ChatMessage(role=MessageRole.ASSISTANT, content=response)
                )
                return {
                    "response": response,
                    "updated_docs": False,
                    "update_details": None,
                }

        # If we have a current project, treat message as a note
        if self.current_project:
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
            return {
                "response": response,
                "updated_docs": False,
                "update_details": None,
            }

        # Run through workflow
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
            else:
                response = "Changes committed."
                update_details = None
        else:
            response = "No changes were made."
            update_details = None

        self.history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response))
        return {
            "response": response,
            "updated_docs": workflow_result.get("committed", False),
            "update_details": update_details,
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
                projects = _list_projects(self.data_dir)
                if projects:
                    project_list = ", ".join(projects)
                    response = f"{decision.response}\n\nTo save this, first select a project:\n`/use project-name`\n\nAvailable: {project_list}"
                else:
                    response = f"{decision.response}\n\nTo save this, first create a project:\n`/use project-name`"

        self.history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response))
        return {
            "response": response,
            "updated_docs": False,
            "update_details": update_details,
        }

    def clear_history(self) -> None:
        """Clear the chat history."""
        self.history.clear()


async def run_chat_session(data_dir: Path) -> None:
    """Run an interactive chat session.

    This is designed to be called from the CLI.
    """
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    console = Console()
    session = ChatSession(data_dir=data_dir)

    console.print(
        Panel(
            "[bold green]Laibrary Chat[/bold green]\n\n"
            "Commands:\n"
            "  [bold]/use project[/bold] - Select a project\n"
            "  [bold]/list[/bold] - Show available projects\n"
            "  [bold]/project note[/bold] - Add note to specific project\n"
            "  [bold]/quit[/bold] - Exit\n"
            "  [bold]/clear[/bold] - Clear history\n\n"
            "Once a project is selected, just type your notes!",
            title="Welcome",
            border_style="green",
        )
    )

    while True:
        # Show current project in prompt
        if session.current_project:
            prompt_prefix = f"[bold blue]({session.current_project})[/bold blue] "
        else:
            prompt_prefix = "[dim](no project)[/dim] "

        try:
            user_input = console.input(
                f"\n{prompt_prefix}[bold blue]>[/bold blue] "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() == "/quit":
            console.print("[dim]Goodbye![/dim]")
            break

        if user_input.lower() == "/clear":
            session.clear_history()
            console.print("[dim]Chat history cleared.[/dim]")
            continue

        with console.status("[dim]Thinking...[/dim]"):
            result = await session.send_message(user_input)

        console.print()
        console.print(Markdown(result["response"]))

        if result["update_details"]:
            console.print(
                f"\n[dim]Committed: {result['update_details']['commit_message']}[/dim]"
            )
