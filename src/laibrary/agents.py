"""Agents for the graph RAG system."""

import os

from dotenv import load_dotenv
from pydantic_ai import Agent

load_dotenv()

title_agent = Agent(
    os.environ["MODEL"],
    output_type=str,
    system_prompt="""Generate a concise title (5-10 words) summarizing the main topic
or theme of this note. The title should capture the essence of the content.

Return ONLY the title text, nothing else.""",
)

summary_agent = Agent(
    os.environ["MODEL"],
    output_type=str,
    system_prompt="""Create a comprehensive summary synthesizing the provided notes
about a specific topic.

Guidelines:
- Organize the summary by themes or subtopics, NOT by individual notes
- Synthesize information across all notes to create a cohesive narrative
- Note areas of uncertainty, contradiction, or evolving understanding
- Use clear, concise language
- Include a "Sources" section at the end listing the note IDs that contributed

Format:
# [Topic Title]

[Main summary content organized by themes]

## Sources
- [Note ID 1]: [Brief description]
- [Note ID 2]: [Brief description]
""",
)

reconcile_agent = Agent(
    os.environ["MODEL"],
    output_type=str,
    system_prompt="""Update an existing summary with new information from recently
added notes.

You will receive:
- EXISTING_SUMMARY: The current summary content
- NEW_NOTES: Recently added notes that should be incorporated

Guidelines:
- Integrate new information seamlessly into the existing structure
- If new information contradicts existing content, note the evolution of understanding
- Preserve the overall structure of the existing summary
- Add new sections only if genuinely new topics emerge
- Update the Sources section with new note references
- Mark any superseded information appropriately

Return the complete updated summary.""",
)
