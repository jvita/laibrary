"""LangGraph workflow definition for the PKM pipeline."""

from functools import partial
from pathlib import Path

import logfire
from langgraph.graph import END, START, StateGraph

from .config import MAX_RETRIES
from .nodes import (
    architect_node,
    committer_node,
    confirmation_node,
    context_node,
    ingestion_node,
    planner_node,
    selector_node,
    summaries_node,
)
from .schemas import PKMState


def _should_continue(state: PKMState) -> str:
    """Determine if workflow should continue or end due to error."""
    if state.get("error"):
        return "end"
    return "continue"


def _should_retry(state: PKMState) -> str:
    """Determine if we should retry after committer failure."""
    if state.get("committed"):
        logfire.info("Committer succeeded")
        return "end"

    error = state.get("error")
    if not error:
        return "end"

    # Check if it's a retryable edit error
    if (
        "search_block not found" in error
        or "search_block appears multiple times" in error
    ):
        retry_count = state.get("retry_count", 0)
        if retry_count < MAX_RETRIES:
            logfire.warn(
                "Edit failed, will retry",
                retry_count=retry_count + 1,
                max_retries=MAX_RETRIES,
                error=error,
            )
            return "retry"
        logfire.error("Max retries exceeded", error=error)

    return "end"


def _retry_prep(state: PKMState) -> PKMState:
    """Prepare state for retrying the architect."""
    retry_count = state.get("retry_count", 0)
    logfire.info("Preparing retry", attempt=retry_count + 1)
    return {
        **state,
        "error": None,  # Clear error so architect doesn't skip
        "retry_count": retry_count + 1,
        "document_update": None,  # Clear the failed update
        "document_updates": None,  # Clear failed multi-updates
    }


def create_workflow(data_dir: Path | None = None) -> StateGraph:
    """Create the PKM workflow graph.

    Args:
        data_dir: Path to the data directory. Defaults to 'data/'.

    Returns:
        Compiled LangGraph StateGraph
    """
    if data_dir is None:
        data_dir = Path("data")

    # Create partial functions with data_dir bound
    summaries_with_dir = partial(summaries_node, data_dir=data_dir)
    selector_with_dir = partial(selector_node, data_dir=data_dir)
    context_with_dir = partial(context_node, data_dir=data_dir)
    confirmation_with_dir = partial(confirmation_node, data_dir=data_dir)
    architect_with_dir = partial(architect_node, data_dir=data_dir)
    committer_with_dir = partial(committer_node, data_dir=data_dir)

    # Build the graph
    graph = StateGraph(PKMState)

    # Add nodes
    graph.add_node("ingestion", ingestion_node)
    graph.add_node("summaries", summaries_with_dir)
    graph.add_node("selector", selector_with_dir)
    graph.add_node("context", context_with_dir)
    graph.add_node("planner", planner_node)
    graph.add_node("confirmation", confirmation_with_dir)
    graph.add_node("architect", architect_with_dir)
    graph.add_node("committer", committer_with_dir)
    graph.add_node("retry_prep", _retry_prep)

    # Add edges
    # START -> ingestion -> summaries -> selector -> context -> planner -> confirmation -> architect -> committer -> END
    graph.add_edge(START, "ingestion")
    graph.add_conditional_edges(
        "ingestion",
        _should_continue,
        {"continue": "summaries", "end": END},
    )
    graph.add_conditional_edges(
        "summaries",
        _should_continue,
        {"continue": "selector", "end": END},
    )
    graph.add_conditional_edges(
        "selector",
        _should_continue,
        {"continue": "context", "end": END},
    )
    graph.add_conditional_edges(
        "context",
        _should_continue,
        {"continue": "planner", "end": END},
    )
    graph.add_conditional_edges(
        "planner",
        _should_continue,
        {"continue": "confirmation", "end": END},
    )
    graph.add_conditional_edges(
        "confirmation",
        _should_continue,
        {"continue": "architect", "end": END},
    )
    graph.add_conditional_edges(
        "architect",
        _should_continue,
        {"continue": "committer", "end": END},
    )
    # Committer can either succeed, fail fatally, or retry
    graph.add_conditional_edges(
        "committer",
        _should_retry,
        {"retry": "retry_prep", "end": END},
    )
    graph.add_edge("retry_prep", "architect")

    return graph.compile()


async def run_workflow(user_input: str, data_dir: Path | None = None) -> PKMState:
    """Run the PKM workflow with user input.

    Args:
        user_input: The user's note to process
        data_dir: Path to the data directory. Defaults to 'data/'.

    Returns:
        Final workflow state
    """
    workflow = create_workflow(data_dir)
    initial_state: PKMState = {"user_input": user_input}
    result = await workflow.ainvoke(initial_state)
    return result


async def run_workflow_with_state(
    initial_state: PKMState, data_dir: Path | None = None
) -> PKMState:
    """Run the PKM workflow with custom initial state.

    Args:
        initial_state: Initial workflow state (must include user_input)
        data_dir: Path to the data directory. Defaults to 'data/'.

    Returns:
        Final workflow state
    """
    workflow = create_workflow(data_dir)
    result = await workflow.ainvoke(initial_state)
    return result
