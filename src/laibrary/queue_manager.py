"""Message queue manager for handling sequential message processing."""

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class MessageStatus(Enum):
    """Status of a queued message."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class QueuedMessage:
    """Represents a message in the queue."""

    message_id: int
    content: str
    status: MessageStatus
    error: str | None = None
    result: dict[str, Any] | None = None


class MessageQueueManager:
    """Manages a sequential queue of messages for processing."""

    def __init__(self, session, data_dir: Path):
        """Initialize the queue manager.

        Args:
            session: ChatSession instance to use for processing messages
            data_dir: Data directory path
        """
        self.session = session
        self.data_dir = data_dir
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.messages: dict[int, QueuedMessage] = {}
        self.next_message_id = 1
        self.worker_task: asyncio.Task | None = None
        self._shutdown = False

    async def enqueue_message(self, content: str) -> int:
        """Add a message to the queue.

        Args:
            content: Message content to queue

        Returns:
            Message ID assigned to this message
        """
        message_id = self.next_message_id
        self.next_message_id += 1

        # Create queued message
        queued_msg = QueuedMessage(
            message_id=message_id,
            content=content,
            status=MessageStatus.QUEUED,
        )
        self.messages[message_id] = queued_msg

        # Add to queue
        await self.queue.put(message_id)

        # Start worker if not running
        if self.worker_task is None or self.worker_task.done():
            self.worker_task = asyncio.create_task(self._process_queue())

        return message_id

    async def _process_queue(self):
        """Worker task that processes messages sequentially."""
        while not self._shutdown:
            try:
                # Get next message from queue with timeout
                try:
                    message_id = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # No messages in queue, check shutdown and continue
                    continue

                msg = self.messages[message_id]

                # Mark as processing
                msg.status = MessageStatus.PROCESSING

                try:
                    # Process the message
                    result = await self.session.send_message(msg.content)

                    # Mark as completed
                    msg.status = MessageStatus.COMPLETED
                    msg.result = result

                except Exception as e:
                    # Mark as failed
                    msg.status = MessageStatus.FAILED
                    msg.error = str(e)

                finally:
                    # Mark task as done in queue
                    self.queue.task_done()

            except Exception:
                # Catch any unexpected errors to prevent worker from dying
                # Silently continue to avoid cluttering console
                continue

    def get_queue_status(self) -> dict[str, Any]:
        """Get current queue status.

        Returns:
            Dictionary with queue statistics and message lists
        """
        queued_messages = []
        processing_messages = []
        completed_count = 0
        failed_count = 0

        for msg in self.messages.values():
            if msg.status == MessageStatus.QUEUED:
                queued_messages.append({"id": msg.message_id, "content": msg.content})
            elif msg.status == MessageStatus.PROCESSING:
                processing_messages.append(
                    {"id": msg.message_id, "content": msg.content}
                )
            elif msg.status == MessageStatus.COMPLETED:
                completed_count += 1
            elif msg.status == MessageStatus.FAILED:
                failed_count += 1

        return {
            "total_messages": len(self.messages),
            "queued_messages": queued_messages,
            "processing_messages": processing_messages,
            "completed_count": completed_count,
            "failed_count": failed_count,
        }

    def get_pending_count(self) -> int:
        """Get count of queued + processing messages.

        Returns:
            Number of messages that are queued or currently processing
        """
        return sum(
            1
            for msg in self.messages.values()
            if msg.status in (MessageStatus.QUEUED, MessageStatus.PROCESSING)
        )

    async def shutdown(self, timeout: float = 30.0):
        """Gracefully shutdown the queue manager.

        Waits for all queued messages to complete processing.

        Args:
            timeout: Maximum time to wait for queue to drain (seconds)
        """
        self._shutdown = True

        try:
            # Wait for queue to be empty
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            # Timeout occurred but avoid cluttering console
            pass

        # Cancel worker task if still running
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
