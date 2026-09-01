"""WebSocket session with JWT authentication."""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from config.settings import get_settings
from core.jwt import JWTManager

router = APIRouter()


@router.websocket("/ws/v1/session")
async def session_ws(
    websocket: WebSocket,
    token: str = Query(...),
    session_id: str | None = Query(default=None),
) -> None:
    settings = get_settings()
    jwt_mgr = JWTManager(settings)
    try:
        claims = jwt_mgr.decode_access_token(token)
    except ValueError:
        await websocket.close(code=4001, reason="unauthorized")
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "session.connected",
            "session_id": session_id,
            "user_id": claims.sub,
            "merchant_id": claims.merchant_id,
        }
    )

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
