"""Main entry point for laibrary tools."""

import asyncio
import json
from pathlib import Path

from langgraph.graph import START, StateGraph

from .agents import categorization_agent, update_agent
from .data_models import Database, Idea, IdeaUpdateState, Note, NoteProcessingState


def initialize_database(state: Database) -> Database:
    """Initializes the laibrary database."""

    # Create the base data/ folder if necessary
    state.path.mkdir(exist_ok=True)

    # Load ideas catalog, if it exists
    ideas_folder = state.path / "ideas"
    ideas_folder.mkdir(exist_ok=True)
    catalog = ideas_folder / "catalog.json"
    if catalog.exists():
        ideas = [Idea(**idea_json) for idea_json in json.loads(catalog.read_text())]
        print(f"Found {len(ideas)} idea{'' if len(ideas) == 1 else 's'} catalog.")
    else:
        print("Creating empty ideas catalog.")
        ideas = []
        catalog.write_text(json.dumps(ideas, indent=4))

    # Load pointers to Notes; create if doesn't exist
    notes_folder = state.path / "raw"
    notes_folder.mkdir(exist_ok=True)
    notes = [Note(path=note_path) for note_path in notes_folder.rglob("*.md")]
    print(f"Found {len(notes)} note{'' if len(notes) == 1 else 's'}.")

    return {"ideas": ideas, "notes": notes}


async def process_notes(state: Database):
    """Process each note sequentially."""
    current_ideas = list(state.ideas)  # Make a mutable copy
    for note in state.notes:
        new_ideas = await process_note(
            NoteProcessingState(
                database_path=state.path,
                existing_ideas=current_ideas,
                note=note,
            )
        )
        current_ideas.extend(new_ideas)


async def process_note(state: NoteProcessingState) -> list[Idea]:
    """Read a note and generate a list of related idea categories."""

    note = state.note
    existing_ideas = state.existing_ideas

    ideas_list = "\n".join(
        f"- {idea.name}: {idea.description}" for idea in existing_ideas
    )

    with open(note.path) as f:
        content = "\n".join(f.readlines())

    prompt = (
        f"Here is the contents of the new note:\n```{content}\n```\n"
        f"And here are the current idea categories:\n```{ideas_list}\n```"
    )

    results = await categorization_agent.run(user_prompt=prompt)

    # Process each idea sequentially
    new_ideas = []
    for idea in results.output:
        idea_state = IdeaUpdateState(
            database_path=state.database_path, idea=idea, note=note
        )
        if idea.is_new:
            new_idea = await create_new_idea(idea_state)
            new_ideas.append(new_idea)
        else:
            await update_existing_idea(idea_state)

    return new_ideas


async def create_new_idea(state: IdeaUpdateState) -> Idea:
    """Creates a new idea."""

    # Add the new idea to the catalog
    catalog_file = state.database_path / "ideas" / "catalog.json"
    catalog = json.loads(catalog_file.read_text())
    catalog.append(state.idea.model_dump())
    catalog_file.write_text(json.dumps(catalog, indent=4))

    # Create new (mostly empty) idea file
    idea_file = state.database_path / "ideas" / Path(f"{state.idea.name}.md")
    if idea_file.exists():
        raise RuntimeError(
            f"Trying to create new idea `{state.idea.name}`, but already exists."
        )

    idea_file.write_text(f"Description: {state.idea.description}")

    # Update the idea with content from the note
    await update_existing_idea(state)

    return state.idea


async def update_existing_idea(state: IdeaUpdateState):
    """Updates an existing idea using content from a note."""

    idea_path = state.database_path / "ideas" / f"{state.idea.name}.md"
    idea_content = idea_path.read_text()

    note_content = state.note.path.read_text()

    prompt = f"SOURCE content:\n```{note_content}\n```\nTARGET content:\n```{idea_content}\n```"

    result = await update_agent.run(prompt)

    idea_path.write_text(result.output)


def build_graph() -> StateGraph:
    """Build and compile the LangGraph workflow."""

    builder = StateGraph(Database)

    # Add nodes
    builder.add_node("initialize_database", initialize_database)
    builder.add_node("process_notes", process_notes)

    # Add edges
    builder.add_edge(START, "initialize_database")
    builder.add_edge("initialize_database", "process_notes")

    # Compile graph
    return builder.compile()


async def main():
    """Execute the LangGraph workflow."""

    graph = build_graph()

    await graph.ainvoke(Database(path=Path("./data")))


if __name__ == "__main__":
    asyncio.run(main())
