"""Main entry point for laibrary tools."""

import asyncio
import json
from pathlib import Path

from langgraph.graph import START, StateGraph

from .data_models import Database, Idea, Note

"""TODO:
- typer?
- check if new/unprocessed files.
"""


def initialize_database(state: Database) -> Database:
    """Initializes the laibrary database."""

    # Create the base data/ folder if necessary
    state.path.mkdir(exist_ok=True)

    # Load ideas catalog, if it exists
    ideas_catalog = state.path / "ideas.json"
    if ideas_catalog.exists():
        print("Loading ideas catalog...")
        with open(ideas_catalog) as f:
            ideas = [Idea(**idea_json) for idea_json in json.load(f)]
    else:
        print("Creating empty ideas catalog...")
        ideas = []
        with open(ideas_catalog, "w") as f:
            json.dump(ideas, f)  # empty catalog

    # Load pointers to Notes; create if doesn't exist
    notes_folder = state.path / "raw"
    notes_folder.mkdir(exist_ok=True)
    notes = [Note(path=note_path) for note_path in notes_folder.rglob("*.md")]

    return {"ideas": ideas, "notes": notes}


def categorize_notes(state: Database) -> Idea:
    """Reads all notes and generates a list of related idea categories for each."""
    pass


def create_new_ideas(raw_note: Note):
    """Creates a new idea using content from a raw note."""
    pass


def update_existing_ideas(raw_note: Note, idea: Idea):
    """Updates an existing idea using content from a raw note."""
    pass


def build_graph() -> StateGraph:
    """Build and compile the LangGraph workflow."""

    builder = StateGraph(Database)

    # Add nodes
    builder.add_node("initialize_database", initialize_database)
    builder.add_node("categorize_notes", categorize_notes)
    builder.add_node("create_new_ideas", create_new_ideas)
    builder.add_node("update_existing_ideas", update_existing_ideas)

    # Add edges
    builder.add_edge(START, "initialize_database")

    # Compile graph
    return builder.compile()


async def main():
    """Execute the LangGraph workflow."""

    graph = build_graph()

    await graph.ainvoke(Database(path=Path("./data")))


if __name__ == "__main__":
    asyncio.run(main())
