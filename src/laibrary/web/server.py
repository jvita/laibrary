"""Starlette web server for Laibrary PWA with message queueing."""

import asyncio
import json
from pathlib import Path
from weakref import WeakSet

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..chat import ChatSession, _list_projects
from ..queue_manager import MessageQueueManager, MessageStatus

# Module-level singleton state
_session: ChatSession | None = None
_queue_manager: MessageQueueManager | None = None
_lock = asyncio.Lock()
_connected_clients: WeakSet[WebSocket] = WeakSet()
_notifier_task: asyncio.Task | None = None
_last_notified: dict[int, int] = {}  # websocket_id -> last_message_id notified


def _get_session(data_dir: Path) -> ChatSession:
    """Get or create the ChatSession singleton."""
    global _session
    if _session is None:
        _session = ChatSession(data_dir=data_dir)
    return _session


def _get_queue_manager(data_dir: Path) -> MessageQueueManager:
    """Get or create the MessageQueueManager singleton."""
    global _queue_manager
    if _queue_manager is None:
        session = _get_session(data_dir)
        _queue_manager = MessageQueueManager(session, data_dir)
    return _queue_manager


async def _notify_clients():
    """Background task to notify WebSocket clients of completed messages."""
    global _last_notified

    while True:
        await asyncio.sleep(0.5)  # Poll every 500ms

        if _queue_manager is None:
            continue

        # Check for newly completed/failed messages
        notified_ids = []
        for msg_id, msg in list(_queue_manager.messages.items()):
            if msg.status not in (MessageStatus.COMPLETED, MessageStatus.FAILED):
                continue

            # Notify all connected clients that haven't seen this message
            all_notified = True
            for ws in list(_connected_clients):
                ws_id = id(ws)
                last_seen = _last_notified.get(ws_id, 0)

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
                                    "update_details": msg.result.get("update_details"),
                                    "current_project": _session.current_project
                                    if _session
                                    else None,
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
                        _last_notified[ws_id] = msg_id
                    except Exception:
                        # Client disconnected, will be cleaned up
                        all_notified = False
                        pass
                # else: already notified this client

            if all_notified:
                notified_ids.append(msg_id)

        # Remove fully-notified messages so they aren't re-sent on reconnect
        for msg_id in notified_ids:
            _queue_manager.messages.pop(msg_id, None)


async def websocket_endpoint(websocket: WebSocket) -> None:
    """Handle WebSocket connections for real-time chat with queueing."""
    global _notifier_task

    await websocket.accept()
    _connected_clients.add(websocket)
    _last_notified[id(websocket)] = 0

    data_dir = websocket.app.state.data_dir
    queue_manager = _get_queue_manager(data_dir)

    # Start notifier task if not running
    if _notifier_task is None or _notifier_task.done():
        _notifier_task = asyncio.create_task(_notify_clients())

    try:
        # Send initial status
        session = _get_session(data_dir)
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
                async with _lock:
                    await session.end_session()
                    session.clear_history()
                    if session.session_manager:
                        session.session_manager.start_session()
                await websocket.send_json({"type": "cleared"})
                continue

            if (
                stripped in ("/list", "/projects")
                or stripped.startswith("/use ")
                or stripped == "/read"
                or stripped.startswith("/read ")
                or (stripped.startswith("/") and " " not in stripped)
            ):
                # Process immediately (these are fast operations)
                async with _lock:
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
        _connected_clients.discard(websocket)
        _last_notified.pop(id(websocket), None)


async def api_message(request) -> JSONResponse:
    """HTTP POST for sending messages (queued)."""
    data_dir = request.app.state.data_dir
    queue_manager = _get_queue_manager(data_dir)
    session = _get_session(data_dir)

    try:
        body = await request.json()
        user_message = body.get("message", "")

        if not user_message:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        # Check for immediate commands
        stripped = user_message.strip().lower()

        if stripped == "/clear":
            async with _lock:
                await session.end_session()
                session.clear_history()
                if session.session_manager:
                    session.session_manager.start_session()
            return JSONResponse({"type": "cleared"})

        if (
            stripped in ("/list", "/projects")
            or stripped.startswith("/use ")
            or stripped == "/read"
            or stripped.startswith("/read ")
            or (stripped.startswith("/") and " " not in stripped)
        ):
            async with _lock:
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
    data_dir = request.app.state.data_dir
    queue_manager = _get_queue_manager(data_dir)
    session = _get_session(data_dir)

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
    data_dir = request.app.state.data_dir
    queue_manager = _get_queue_manager(data_dir)
    session = _get_session(data_dir)

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
    projects = _list_projects(data_dir)
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

    return app
