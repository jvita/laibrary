"""LangGraph workflow definition for the PKM pipeline."""

from functools import partial
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .nodes import (
    architect_node,
    committer_node,
    context_node,
    ingestion_node,
)
from .schemas import PKMState


def _should_continue(state: PKMState) -> str:
    """Determine if workflow should continue or end due to error/command."""
    if state.get("error"):
        return "end"

    # Check for special commands that don't need further processing
    command = state.get("command")
    if command == "list":
        return "end"

    return "continue"


def create_workflow(data_dir: Path | None = None) -> StateGraph:
    """Create the PKM workflow graph.

    Simplified workflow: START → ingestion → context → architect → committer → END

    Args:
        data_dir: Path to the data directory. Defaults to 'data/'.

    Returns:
        Compiled LangGraph StateGraph
    """
    if data_dir is None:
        data_dir = Path("data")

    # Create partial functions with data_dir bound
    ingestion_with_dir = partial(ingestion_node, data_dir=data_dir)
    context_with_dir = partial(context_node, data_dir=data_dir)
    architect_with_dir = partial(architect_node, data_dir=data_dir)
    committer_with_dir = partial(committer_node, data_dir=data_dir)

    # Build the graph
    graph = StateGraph(PKMState)

    # Add nodes
    graph.add_node("ingestion", ingestion_with_dir)
    graph.add_node("context", context_with_dir)
    graph.add_node("architect", architect_with_dir)
    graph.add_node("committer", committer_with_dir)

    # Add edges: START → ingestion → context → architect → committer → END
    graph.add_edge(START, "ingestion")
    graph.add_conditional_edges(
        "ingestion",
        _should_continue,
        {"continue": "context", "end": END},
    )
    graph.add_conditional_edges(
        "context",
        _should_continue,
        {"continue": "architect", "end": END},
    )
    graph.add_conditional_edges(
        "architect",
        _should_continue,
        {"continue": "committer", "end": END},
    )
    graph.add_edge("committer", END)

    return graph.compile()


async def run_workflow(user_input: str, data_dir: Path | None = None) -> PKMState:
    """Run the PKM workflow with user input.

    Args:
        user_input: The user's note to process (should include /project prefix)
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
