"""Agents used for note/idea management."""

import os

from dotenv import load_dotenv
from pydantic_ai import Agent

from .data_models import Idea

load_dotenv()

categorization_agent = Agent(
    os.environ["MODEL"],
    output_type=list[Idea],
    system_prompt="""You are a librarian tasked with maintaining a digital library.

Your job is to:
- Maintain a catalog of unique ideas/concepts/topics
- Categorize new documents by either marking them as being related to an existing
idea/concept/topic, or classifying them as an entirely new idea/concept/topic.
""",
)

update_agent = Agent(
    os.environ["MODEL"],
    output_type=str,
    system_prompt="""You are an assistant whose
job is to maintain 'living documents' by taking in a SOURCE document and using it to
update a TARGET document. Your goal is for the TARGET document to provide a user with a
comprehensive overview of an idea/concept/topic.

IMPORTANT:
- Avoid removing information. Instead, if information from SOURCE supersedes/contradicts
existing information in TARGET, strike out the outdated text using ~~~<text>~~~ syntax.
""",
)
