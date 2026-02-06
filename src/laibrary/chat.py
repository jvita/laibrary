"""Chat interface for the PKM system."""

import asyncio
import os
from dataclasses import dataclass, field
from difflib import get_close_matches
from enum import Enum
from pathlib import Path

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .config import MAX_RETRIES, QUERY_SETTINGS, ROUTER_SETTINGS
from .git_wrapper import IsolatedGitRepo
from .prompts import QUERY_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT
from .session_manager import SessionManager
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


def _is_query_intent(message: str) -> bool:
    """Check if message appears to be a query using heuristics with fuzzy matching.

    Handles typos by using fuzzy string matching on the first word.
    """
    lower = message.lower().strip()

    # Check for question mark
    if lower.endswith("?"):
        return True

    # Extract first word
    first_word = lower.split()[0] if lower.split() else ""

    # Query keywords to match against
    query_starters = [
        "what",
        "when",
        "where",
        "who",
        "why",
        "how",
        "show",
        "tell",
        "list",
        "display",
        "get",
        "find",
        "search",
        "check",
        "whats",
        "what's",
        "wheres",
        "where's",
        "whos",
        "who's",
        "hows",
        "how's",
    ]

    # Exact match
    if first_word in query_starters:
        return True

    # Handle multi-word query starters (e.g., "show me", "tell me")
    first_two_words = " ".join(lower.split()[:2]) if len(lower.split()) >= 2 else ""
    if first_two_words in ["show me", "tell me"]:
        return True

    # Fuzzy match for typos (allow 1-2 character difference)
    # Use cutoff of 0.7 to be reasonably strict but allow common typos
    close_matches = get_close_matches(first_word, query_starters, n=1, cutoff=0.7)
    if close_matches:
        return True

    return False


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

    # Also load session documents for cross-project connections
    session_documents: dict[str, str] = {}
    for file_path in repo.list_files("sessions/*.md"):
        content = repo.get_file_content(file_path)
        if content is not None:
            session_documents[file_path] = content

    if not documents and not session_documents:
        return "I don't have any project documents yet. Create one with /use project-name and add some notes!"

    # Build context from documents
    doc_context_parts = ["# Your Knowledge Base\n"]
    for file_path, content in documents.items():
        doc_context_parts.append(f"\n## Document: {file_path}\n\n{content}\n")

    # Add session documents
    if session_documents:
        doc_context_parts.append("\n# Chat Session History\n")
        for file_path, content in session_documents.items():
            doc_context_parts.append(f"\n## Session: {file_path}\n\n{content}\n")

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
    session_manager: SessionManager | None = field(default=None)

    def __post_init__(self) -> None:
        """Initialize session manager."""
        if self.session_manager is None:
            self.session_manager = SessionManager(data_dir=self.data_dir)

    def _project_exists(self, project_name: str) -> bool:
        """Check if a project exists."""
        repo = IsolatedGitRepo(self.data_dir)
        return repo.file_exists(f"projects/{project_name}.md")

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
                if self._project_exists(project_name):
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

            content = _load_project(self.data_dir, project_name)
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
                if self._project_exists(project_name):
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

        # Run through workflow with session_id for bidirectional linking
        user_input = f"/{self.current_project} {note_content}"
        session_id = (
            self.session_manager.get_current_session_id()
            if self.session_manager
            else None
        )
        initial_state = {
            "user_input": user_input,
            "confirmation_mode": "auto",  # Auto-confirm in chat mode
            "session_id": session_id,
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
                projects = _list_projects(self.data_dir)
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


async def _display_completed_messages(
    queue_manager,
    displayed_messages: set,
    console,
):
    """Background task to poll for and display completed messages."""
    from prompt_toolkit import print_formatted_text
    from prompt_toolkit.formatted_text import FormattedText

    from .queue_manager import MessageStatus

    while True:
        await asyncio.sleep(0.5)  # Poll every 500ms

        for msg_id, msg in queue_manager.messages.items():
            if msg_id in displayed_messages:
                continue

            if msg.status == MessageStatus.COMPLETED:
                displayed_messages.add(msg_id)
                # Use prompt_toolkit's print instead of Rich to avoid ANSI conflicts
                print_formatted_text(
                    FormattedText([("ansigreen", f"\n✓ Message #{msg_id}:\n")])
                )
                print_formatted_text(msg.result["response"])
                if msg.result.get("update_details"):
                    print_formatted_text(
                        FormattedText(
                            [
                                (
                                    "",
                                    f"Committed: {msg.result['update_details']['commit_message']}\n",
                                )
                            ]
                        )
                    )

            elif msg.status == MessageStatus.FAILED:
                displayed_messages.add(msg_id)
                print_formatted_text(
                    FormattedText([("ansired", f"\n✗ Message #{msg_id} failed:\n")])
                )
                print_formatted_text(FormattedText([("ansired", f"{msg.error}\n")]))


def _display_queue_status(console, queue_manager):
    """Display /status command output."""
    status = queue_manager.get_queue_status()

    console.print("\n[bold]Queue Status:[/bold]")
    console.print(f"  Total messages: {status['total_messages']}")
    console.print(f"  Queued: {len(status['queued_messages'])}")
    console.print(f"  Processing: {len(status['processing_messages'])}")
    console.print(f"  Completed: {status['completed_count']}")
    console.print(f"  Failed: {status['failed_count']}")

    if status["queued_messages"]:
        console.print("\n[bold]Queued:[/bold]")
        for msg in status["queued_messages"]:
            preview = (
                msg["content"][:50] + "..."
                if len(msg["content"]) > 50
                else msg["content"]
            )
            console.print(f"  #{msg['id']}: {preview}")

    if status["processing_messages"]:
        console.print("\n[bold]Processing:[/bold]")
        for msg in status["processing_messages"]:
            preview = (
                msg["content"][:50] + "..."
                if len(msg["content"]) > 50
                else msg["content"]
            )
            console.print(f"  #{msg['id']}: {preview}")


async def run_chat_session(data_dir: Path) -> None:
    """Run an interactive chat session with message queuing.

    This is designed to be called from the CLI.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.patch_stdout import patch_stdout
    from rich.console import Console
    from rich.panel import Panel

    from .queue_manager import MessageQueueManager

    console = Console()
    session = ChatSession(data_dir=data_dir)
    queue_manager = MessageQueueManager(session, data_dir)
    displayed_messages = set()  # Track what we've shown
    prompt_session = PromptSession()  # Create prompt_toolkit session

    # Start background task to display results
    display_task = asyncio.create_task(
        _display_completed_messages(queue_manager, displayed_messages, console)
    )

    console.print(
        Panel(
            "[bold green]Laibrary Chat[/bold green]\n\n"
            "Commands:\n"
            "  [bold]/use project[/bold] - Select a project\n"
            "  [bold]/list[/bold] - Show available projects\n"
            "  [bold]/read [project][/bold] - Print project document\n"
            "  [bold]/project note[/bold] - Add note to specific project\n"
            "  [bold]/status[/bold] - Show queue status\n"
            "  [bold]/quit[/bold] - Exit\n"
            "  [bold]/clear[/bold] - Clear history\n\n"
            "Once a project is selected, just type your notes!\n"
            "Messages are queued and processed sequentially.",
            title="Welcome",
            border_style="green",
        )
    )

    try:
        while True:
            # Show current project and queue status in prompt
            if session.current_project:
                prompt_prefix = f"[bold blue]({session.current_project})[/bold blue] "
            else:
                prompt_prefix = "[dim](no project)[/dim] "

            pending = queue_manager.get_pending_count()
            if pending > 0:
                prompt_prefix = f"[dim]({pending} pending)[/dim] {prompt_prefix}"

            try:
                # Build styled prompt with colors
                if session.current_project:
                    project_part = (
                        f"<ansiblue><b>({session.current_project})</b></ansiblue>"
                    )
                else:
                    project_part = "<ansigray>(no project)</ansigray>"

                if pending > 0:
                    prompt_html = f"<ansigray>({pending} pending)</ansigray> {project_part} <ansiblue><b>></b></ansiblue> "
                else:
                    prompt_html = f"{project_part} <ansiblue><b>></b></ansiblue> "

                # Get input with proper line editing support
                with patch_stdout():
                    user_input = await prompt_session.prompt_async(
                        HTML(prompt_html),
                        multiline=False,
                    )
                user_input = user_input.strip()
            except (EOFError, KeyboardInterrupt):
                # End session on interrupt
                session_path = await session.end_session()
                if session_path:
                    console.print(f"\n[dim]Session saved to {session_path}[/dim]")
                console.print("[dim]Goodbye![/dim]")
                break

            if not user_input:
                continue

            # Handle special commands immediately (not queued)
            if user_input.lower() == "/quit":
                # End session and persist before exiting
                session_path = await session.end_session()
                if session_path:
                    console.print(f"[dim]Session saved to {session_path}[/dim]")
                console.print("[dim]Goodbye![/dim]")
                break

            elif user_input.lower() == "/status":
                _display_queue_status(console, queue_manager)
                continue

            elif user_input.lower() == "/clear":
                # End current session and start a new one
                session_path = await session.end_session()
                if session_path:
                    console.print(f"[dim]Session saved to {session_path}[/dim]")
                session.clear_history()
                displayed_messages.clear()
                console.print("[dim]Chat history cleared. New session started.[/dim]")
                continue

            elif user_input.lower() in ("/list", "/projects"):
                # List projects immediately
                projects = _list_projects(data_dir)
                if projects:
                    console.print("\n[bold]Available projects:[/bold]")
                    for project_name in projects:
                        console.print(f"  - {project_name}")
                else:
                    console.print("[dim]No projects found.[/dim]")
                continue

            elif user_input.lower() == "/read" or user_input.lower().startswith(
                "/read "
            ):
                # Print project document immediately
                result = await session.send_message(user_input)
                from rich.markdown import Markdown

                console.print(Markdown(result["response"]))
                continue

            elif user_input.lower().startswith("/use "):
                # Handle project selection immediately (don't queue)
                result = await session.send_message(user_input)
                from rich.markdown import Markdown

                console.print(Markdown(result["response"]))
                continue

            elif user_input.startswith("/") and " " not in user_input:
                # Handle /<project> (no note) - just project switch, immediate
                result = await session.send_message(user_input)
                from rich.markdown import Markdown

                console.print(Markdown(result["response"]))
                continue

            # Queue the message for processing
            msg_id = await queue_manager.enqueue_message(user_input)
            console.print(f"[dim]Queued message #{msg_id}[/dim]")

    finally:
        # Graceful shutdown
        pending = queue_manager.get_pending_count()
        if pending > 0:
            console.print(
                f"\n[yellow]Waiting for {pending} pending messages...[/yellow]"
            )
        await queue_manager.shutdown(timeout=30.0)
        display_task.cancel()
        try:
            await display_task
        except asyncio.CancelledError:
            pass
