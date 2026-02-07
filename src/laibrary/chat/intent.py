"""Query intent detection, routing agent, and query handling."""

from difflib import get_close_matches
from pathlib import Path

import logfire

from ..config import QUERY_SETTINGS, ROUTER_SETTINGS, create_agent
from ..git_wrapper import IsolatedGitRepo
from ..prompts import QUERY_SYSTEM_PROMPT, ROUTER_SYSTEM_PROMPT
from .models import ChatMessage, MessageRole, RouterDecision


def _create_router_agent():
    """Create the router agent that decides how to handle messages."""
    return create_agent(
        system_prompt=ROUTER_SYSTEM_PROMPT,
        output_type=RouterDecision,
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
    query_agent = create_agent(
        system_prompt=QUERY_SYSTEM_PROMPT,
        model_settings=QUERY_SETTINGS,
    )

    try:
        result = await query_agent.run(query_prompt)
        return result.output
    except Exception as e:
        logfire.error("Query agent failed", error=str(e))
        return f"I encountered an error searching your documents: {e}"
