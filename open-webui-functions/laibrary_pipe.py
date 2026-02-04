"""Laibrary Pipe Function for Open WebUI.

This Pipe routes all messages through laibrary's ChatSession, enabling:
- Native slash command support (/use project, /list, etc.)
- Automatic intent routing (update, query, chat)
- Transparent integration - LLM doesn't need to call tools

Installation:
1. Go to Admin Panel → Functions → Add Function
2. Paste this code
3. Save and enable
4. Select "laibrary" as the model in Open WebUI chat
"""

import asyncio
from collections.abc import AsyncGenerator

from pydantic import BaseModel, Field


class Pipe:
    """Laibrary Pipe that routes messages through ChatSession."""

    class Valves(BaseModel):
        """Configuration valves for the Pipe."""

        LAIBRARY_DATA_DIR: str = Field(
            default="/app/laibrary-data",
            description="Path to the laibrary data directory",
        )
        MODEL: str = Field(
            default="ollama:gpt-oss:20b",
            description="Model to use for laibrary LLM calls",
        )
        OLLAMA_BASE_URL: str = Field(
            default="http://host.docker.internal:11434/v1",
            description="Base URL for Ollama API (use host.docker.internal for Docker Desktop, or ollama container name)",
        )

    def __init__(self):
        """Initialize the Pipe and its configuration valves."""
        self.valves = self.Valves()
        self._session = None
        self._lock = asyncio.Lock()

    def _get_session(self):
        """Get or create the ChatSession singleton."""
        if self._session is None:
            import os
            from pathlib import Path

            # Set environment variables BEFORE importing laibrary
            # Use direct assignment to override any existing values from docker-compose
            os.environ["MODEL"] = self.valves.MODEL
            os.environ["OLLAMA_BASE_URL"] = self.valves.OLLAMA_BASE_URL
            # Dummy API key needed for pydantic-ai's OpenAI-compatible interface
            os.environ.setdefault("OPENAI_API_KEY", "ollama")

            # Debug: log the env vars
            print(f"[laibrary-pipe] MODEL={os.environ.get('MODEL')}")
            print(
                f"[laibrary-pipe] OLLAMA_BASE_URL={os.environ.get('OLLAMA_BASE_URL')}"
            )

            from laibrary.chat import ChatSession

            data_dir = Path(self.valves.LAIBRARY_DATA_DIR)
            self._session = ChatSession(data_dir=data_dir)

        return self._session

    async def pipe(
        self,
        body: dict,
        __user__: dict,
    ) -> str | AsyncGenerator:
        """Process incoming messages through laibrary.

        Args:
            body: Request body containing messages
            __user__: User information from Open WebUI

        Returns:
            Response string or async generator for streaming
        """
        messages = body.get("messages", [])
        if not messages:
            return "No message received."

        # Get the latest user message
        user_message = messages[-1].get("content", "")
        if not user_message:
            return "Empty message received."

        # Process through laibrary ChatSession with lock for thread safety
        async with self._lock:
            session = self._get_session()
            result = await session.send_message(user_message)

        return result["response"]
