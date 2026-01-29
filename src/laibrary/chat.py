"""Chat interface for the PKM system."""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .git_wrapper import IsolatedGitRepo
from .nodes.summaries import SummaryCache, generate_summary
from .prompts import QUERY_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT, SELECTOR_SYSTEM_PROMPT
from .schemas import SelectionResult
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


def _load_documents(
    data_dir: Path, selected_files: list[str] | None = None
) -> dict[str, str]:
    """Load markdown documents from the data directory.

    Args:
        data_dir: Path to the data directory
        selected_files: If provided, only load these files. If None, load all.

    Returns:
        Dict mapping file paths to their content
    """
    repo = IsolatedGitRepo(data_dir)
    context_files: dict[str, str] = {}

    if selected_files is not None:
        # Load only selected files
        for file_path in selected_files:
            content = repo.get_file_content(file_path)
            if content is not None:
                context_files[file_path] = content
    else:
        # Load all markdown files
        for file_path in repo.list_files("**/*.md"):
            content = repo.get_file_content(file_path)
            if content is not None:
                context_files[file_path] = content

    return context_files


async def _ensure_summaries(
    data_dir: Path, documents: dict[str, str]
) -> dict[str, str]:
    """Ensure all documents have summaries, generating missing ones.

    Args:
        data_dir: Path to the data directory
        documents: Dict mapping file paths to their content

    Returns:
        Dict mapping file paths to their summaries
    """
    cache = SummaryCache(data_dir)
    summaries: dict[str, str] = {}

    for file_path, content in documents.items():
        # Try to get cached summary
        summary = cache.get(file_path, content)
        if summary is not None:
            summaries[file_path] = summary
        else:
            # Generate new summary
            try:
                summary = await generate_summary(content)
                cache.set(file_path, content, summary)
                summaries[file_path] = summary
            except Exception as e:
                logfire.warn("Failed to generate summary", file=file_path, error=str(e))
                # Use a fallback summary based on first line
                first_line = content.split("\n")[0].strip()
                if first_line.startswith("#"):
                    first_line = first_line.lstrip("#").strip()
                summaries[file_path] = (
                    first_line[:100] if first_line else "Document content"
                )

    return summaries


async def _select_documents(query: str, summaries: dict[str, str]) -> list[str] | None:
    """Run selector to pick relevant documents for a query.

    Args:
        query: The user's query
        summaries: Dict mapping file paths to their summaries

    Returns:
        List of selected file paths, or None to load all documents
    """
    if not summaries:
        return None

    # Build prompt with summaries
    summary_parts = ["## Available Documents\n"]
    for file_path, summary in summaries.items():
        summary_parts.append(f"- **{file_path}**: {summary}")
    summary_text = "\n".join(summary_parts)

    prompt = f"{summary_text}\n\n## User Query\n{query}"

    try:
        agent = Agent(
            os.environ["MODEL"],
            system_prompt=SELECTOR_SYSTEM_PROMPT,
            output_type=SelectionResult,
        )
        result = await agent.run(prompt)
        selection = result.output

        logfire.info(
            "Selector decision for query",
            selected_count=len(selection.selected_files),
            reasoning=selection.reasoning,
        )

        # Empty selection means load all
        if not selection.selected_files:
            return None

        return selection.selected_files

    except Exception as e:
        logfire.error("Selector failed for query", error=str(e))
        return None


async def _handle_query(
    user_message: str, data_dir: Path, target_hint: str | None = None
) -> str:
    """Handle a query intent by searching documents and answering the question.

    Uses two-stage context loading:
    1. Load summaries and select relevant documents
    2. Load only selected documents for the query agent

    Args:
        user_message: The user's question
        data_dir: Path to the data directory
        target_hint: Optional hint about which documents to focus on

    Returns:
        Natural language answer to the question
    """
    # Stage 1: Load all documents to ensure summaries exist
    all_documents = _load_documents(data_dir)

    if not all_documents:
        return "I don't have any documents in your knowledge base yet. Try adding some notes first!"

    # Ensure summaries exist for all documents
    summaries = await _ensure_summaries(data_dir, all_documents)

    # Select relevant documents based on summaries
    selected_files = await _select_documents(user_message, summaries)

    # Stage 2: Load only selected documents (or all if selection returned None)
    if selected_files is not None:
        documents = _load_documents(data_dir, selected_files)
        logfire.info(
            "Query using selected documents",
            total_docs=len(all_documents),
            selected_docs=len(documents),
        )
    else:
        documents = all_documents
        logfire.info("Query using all documents", total_docs=len(documents))

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

    @logfire.instrument("chat_message")
    async def send_message(self, user_message: str) -> dict:
        """Process a user message and return the response.

        Returns:
            Dict with keys:
                - response: str - The assistant's response
                - updated_docs: bool - Whether documents were updated
                - update_details: dict | None - Details about the update if any
        """
        # Add user message to history
        self.history.append(ChatMessage(role=MessageRole.USER, content=user_message))

        # Build context and route the message
        context = _build_chat_context(self.history[:-1])  # Exclude current message
        prompt = f"{context}\n\n## Current Message\n{user_message}"

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

        # Branch on intent
        match decision.intent:
            case Intent.CHAT:
                # Simple conversation, no document interaction
                response = decision.response

            case Intent.QUERY:
                # Read from documents to answer question
                try:
                    answer = await _handle_query(
                        user_message, self.data_dir, decision.target_hint
                    )
                    response = answer
                except Exception as e:
                    logfire.error("Query handling failed", error=str(e))
                    response = f"I encountered an error searching your documents: {e}"

            case Intent.UPDATE:
                # Run the document update workflow with interactive confirmation
                initial_state = {
                    "user_input": user_message,
                    "confirmation_mode": "interactive",
                }
                workflow_result = await run_workflow_with_state(
                    initial_state, self.data_dir
                )

                if workflow_result.get("error"):
                    logfire.error("Workflow failed", error=workflow_result["error"])
                    response = f"{decision.response}\n\n(Note: I tried to save this but encountered an error: {workflow_result['error']})"
                elif workflow_result.get("committed"):
                    # Check for multi-document updates
                    updates = workflow_result.get("document_updates")
                    if updates:
                        # Multiple files updated
                        file_list = ", ".join([u.target_file for u in updates])
                        commit_msg = updates[0].commit_message if updates else ""
                        update_details = {
                            "files": [u.target_file for u in updates],
                            "commit_message": commit_msg,
                        }
                        response = f"{decision.response}\n\n[Modified: {file_list}]"
                    else:
                        # Single file update (backward compat)
                        update = workflow_result.get("document_update")
                        if update:
                            update_details = {
                                "file": update.target_file,
                                "commit_message": update.commit_message,
                            }
                            response = f"{decision.response}\n\n[Modified: {update.target_file}]"
                        else:
                            response = decision.response
                else:
                    response = f"{decision.response}\n\n(Note: No changes were made to documents)"

        # Add assistant response to history
        self.history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response))

        return {
            "response": response,
            "updated_docs": decision.intent == Intent.UPDATE
            and (update_details is not None),
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
            "Chat with me and I'll help manage your knowledge base.\n"
            "Share ideas, notes, or thoughts - I'll save the important stuff.\n\n"
            "Commands: [bold]/quit[/bold] to exit, [bold]/clear[/bold] to clear history",
            title="Welcome",
            border_style="green",
        )
    )

    while True:
        try:
            user_input = console.input("\n[bold blue]You:[/bold blue] ").strip()
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
        console.print("[bold green]Assistant:[/bold green]")
        console.print(Markdown(result["response"]))

        if result["update_details"]:
            console.print(
                f"\n[dim]Committed: {result['update_details']['commit_message']}[/dim]"
            )
