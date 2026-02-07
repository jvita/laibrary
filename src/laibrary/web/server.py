"""Starlette web server for Laibrary PWA with message queueing."""

import asyncio
import json
import time
from pathlib import Path
from weakref import WeakSet

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..chat import ChatSession
from ..commands import is_immediate_command
from ..projects import list_projects
from ..queue_manager import MessageQueueManager, MessageStatus


def _get_session(app: Starlette) -> ChatSession:
    """Get or create the ChatSession singleton from app.state."""
    if not hasattr(app.state, "session") or app.state.session is None:
        app.state.session = ChatSession(data_dir=app.state.data_dir)
    return app.state.session


def _get_queue_manager(app: Starlette) -> MessageQueueManager:
    """Get or create the MessageQueueManager singleton from app.state."""
    if not hasattr(app.state, "queue_manager") or app.state.queue_manager is None:
        session = _get_session(app)
        app.state.queue_manager = MessageQueueManager(session, app.state.data_dir)
    return app.state.queue_manager


def _get_lock(app: Starlette) -> asyncio.Lock:
    """Get or create the lock from app.state."""
    if not hasattr(app.state, "lock") or app.state.lock is None:
        app.state.lock = asyncio.Lock()
    return app.state.lock


def _get_connected_clients(app: Starlette) -> WeakSet:
    """Get or create the connected clients set from app.state."""
    if (
        not hasattr(app.state, "connected_clients")
        or app.state.connected_clients is None
    ):
        app.state.connected_clients = WeakSet()
    return app.state.connected_clients


def _get_last_notified(app: Starlette) -> dict[int, int]:
    """Get or create the last_notified dict from app.state."""
    if not hasattr(app.state, "last_notified") or app.state.last_notified is None:
        app.state.last_notified = {}
    return app.state.last_notified


async def _notify_clients(app: Starlette):
    """Background task to notify WebSocket clients of completed messages."""
    queue_manager = _get_queue_manager(app)
    connected_clients = _get_connected_clients(app)
    last_notified = _get_last_notified(app)
    session = _get_session(app)

    while True:
        await asyncio.sleep(0.5)  # Poll every 500ms

        try:
            now = time.time()
            cleanup_ids = []

            # Check for newly completed/failed messages
            for msg_id, msg in list(queue_manager.messages.items()):
                if msg.status not in (MessageStatus.COMPLETED, MessageStatus.FAILED):
                    continue

                # Notify all connected clients that haven't seen this message
                clients = list(connected_clients)
                all_notified = True
                for ws in clients:
                    ws_id = id(ws)
                    last_seen = last_notified.get(ws_id, 0)

                    if msg_id > last_seen:
                        try:
                            if msg.status == MessageStatus.COMPLETED:
                                await ws.send_json(
                                    {
                                        "type": "completed",
                                        "message_id": msg_id,
                                        "response": msg.result["response"],
                                        "updated_docs": msg.result.get(
                                            "updated_docs", False
                                        ),
                                        "update_details": msg.result.get(
                                            "update_details"
                                        ),
                                        "current_project": session.current_project,
                                    }
                                )
                            else:
                                await ws.send_json(
                                    {
                                        "type": "failed",
                                        "message_id": msg_id,
                                        "error": msg.error,
                                    }
                                )
                            last_notified[ws_id] = msg_id
                        except Exception:
                            # Client disconnected, will be cleaned up
                            all_notified = False
                    # else: already notified this client

                # Only clean up if at least one client was actually notified,
                # or if the message has been completed for over 60 seconds
                # (fallback to prevent memory leaks when no clients connect)
                timed_out = msg.completed_at and (now - msg.completed_at > 60)
                if (all_notified and len(clients) > 0) or timed_out:
                    cleanup_ids.append(msg_id)

            for msg_id in cleanup_ids:
                queue_manager.messages.pop(msg_id, None)

        except Exception:
            # Prevent the notifier task from dying on unexpected errors
            pass


async def websocket_endpoint(websocket: WebSocket) -> None:
    """Handle WebSocket connections for real-time chat with queueing."""
    app = websocket.app

    await websocket.accept()
    connected_clients = _get_connected_clients(app)
    last_notified = _get_last_notified(app)
    connected_clients.add(websocket)
    last_notified[id(websocket)] = 0

    queue_manager = _get_queue_manager(app)
    session = _get_session(app)
    lock = _get_lock(app)

    # Start notifier task if not running
    if (
        not hasattr(app.state, "notifier_task")
        or app.state.notifier_task is None
        or app.state.notifier_task.done()
    ):
        app.state.notifier_task = asyncio.create_task(_notify_clients(app))

    try:
        # Send initial status
        await websocket.send_json(
            {
                "type": "status",
                "current_project": session.current_project,
                "pending_count": queue_manager.get_pending_count(),
            }
        )

        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            user_message = message_data.get("message", "")

            if not user_message:
                await websocket.send_json({"type": "error", "error": "Empty message"})
                continue

            # Check for immediate commands that don't need queueing
            stripped = user_message.strip().lower()

            if stripped == "/clear":
                async with lock:
                    await session.end_session()
                    session.clear_history()
                    if session.session_manager:
                        session.session_manager.start_session()
                await websocket.send_json({"type": "cleared"})
                continue

            if is_immediate_command(user_message):
                # Process immediately (these are fast operations)
                async with lock:
                    result = await session.send_message(user_message)
                await websocket.send_json(
                    {
                        "type": "immediate",
                        "response": result["response"],
                        "updated_docs": result.get("updated_docs", False),
                        "update_details": result.get("update_details"),
                        "current_project": session.current_project,
                    }
                )
                continue

            # Queue the message for processing
            msg_id = await queue_manager.enqueue_message(user_message)

            # Send acknowledgment
            await websocket.send_json(
                {
                    "type": "queued",
                    "message_id": msg_id,
                    "pending_count": queue_manager.get_pending_count(),
                }
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except Exception:
            pass
    finally:
        connected_clients.discard(websocket)
        last_notified.pop(id(websocket), None)


async def api_message(request) -> JSONResponse:
    """HTTP POST for sending messages (queued)."""
    app = request.app
    queue_manager = _get_queue_manager(app)
    session = _get_session(app)
    lock = _get_lock(app)

    try:
        body = await request.json()
        user_message = body.get("message", "")

        if not user_message:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        # Check for immediate commands
        stripped = user_message.strip().lower()

        if stripped == "/clear":
            async with lock:
                await session.end_session()
                session.clear_history()
                if session.session_manager:
                    session.session_manager.start_session()
            return JSONResponse({"type": "cleared"})

        if is_immediate_command(user_message):
            async with lock:
                result = await session.send_message(user_message)
            return JSONResponse(
                {
                    "type": "immediate",
                    "response": result["response"],
                    "updated_docs": result.get("updated_docs", False),
                    "update_details": result.get("update_details"),
                    "current_project": session.current_project,
                }
            )

        # Queue the message
        msg_id = await queue_manager.enqueue_message(user_message)

        return JSONResponse(
            {
                "type": "queued",
                "message_id": msg_id,
                "pending_count": queue_manager.get_pending_count(),
            }
        )

    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_poll(request) -> JSONResponse:
    """HTTP GET to poll for message results."""
    app = request.app
    queue_manager = _get_queue_manager(app)
    session = _get_session(app)

    # Get the 'since' parameter (last message ID client has seen)
    since = int(request.query_params.get("since", 0))

    # Find completed/failed messages after 'since'
    updates = []
    for msg_id, msg in queue_manager.messages.items():
        if msg_id <= since:
            continue
        if msg.status == MessageStatus.COMPLETED:
            updates.append(
                {
                    "type": "completed",
                    "message_id": msg_id,
                    "response": msg.result["response"],
                    "updated_docs": msg.result.get("updated_docs", False),
                    "update_details": msg.result.get("update_details"),
                }
            )
        elif msg.status == MessageStatus.FAILED:
            updates.append(
                {
                    "type": "failed",
                    "message_id": msg_id,
                    "error": msg.error,
                }
            )

    return JSONResponse(
        {
            "updates": updates,
            "current_project": session.current_project,
            "pending_count": queue_manager.get_pending_count(),
        }
    )


async def api_status(request) -> JSONResponse:
    """Get current session and queue status."""
    app = request.app
    queue_manager = _get_queue_manager(app)
    session = _get_session(app)

    queue_status = queue_manager.get_queue_status()

    return JSONResponse(
        {
            "current_project": session.current_project,
            "history_length": len(session.history),
            "queue": queue_status,
        }
    )


async def api_projects(request) -> JSONResponse:
    """List available projects."""
    data_dir = request.app.state.data_dir
    projects = list_projects(data_dir)
    return JSONResponse({"projects": projects})


def create_app(data_dir: Path) -> Starlette:
    """Create the Starlette application.

    Args:
        data_dir: Path to the laibrary data directory.

    Returns:
        Configured Starlette application.
    """
    static_dir = Path(__file__).parent / "static"

    routes = [
        WebSocketRoute("/ws", websocket_endpoint),
        Route("/api/message", api_message, methods=["POST"]),
        Route("/api/poll", api_poll, methods=["GET"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/projects", api_projects, methods=["GET"]),
        Mount("/", StaticFiles(directory=static_dir, html=True), name="static"),
    ]

    app = Starlette(routes=routes)
    app.state.data_dir = data_dir
    # Initialize state slots (lazily populated on first access)
    app.state.session = None
    app.state.queue_manager = None
    app.state.lock = None
    app.state.connected_clients = None
    app.state.notifier_task = None
    app.state.last_notified = None

    return app
