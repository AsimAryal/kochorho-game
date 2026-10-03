from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import pathlib
import json
import logging

from .game_manager import game_manager, sanitize_nickname

logger = logging.getLogger(__name__)

app = FastAPI()

# --- Setup Paths ---
BASE_DIR = pathlib.Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Create directories if they don't exist (though write_to_file will likely do this for files)
STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Mount Static
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Setup Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Custom close codes understood by the client
CLOSE_GAME_NOT_FOUND = 4000
CLOSE_GAME_IN_PROGRESS = 4001
CLOSE_REPLACED = 4002  # Same player opened the game in another tab

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    return templates.TemplateResponse(request, "index.html")

# --- WebSockets ---

async def receive_message(websocket: WebSocket) -> dict:
    """Reads one JSON message; malformed input is ignored rather than killing the socket."""
    text = await websocket.receive_text()
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@app.websocket("/ws/lobby")
async def websocket_lobby(websocket: WebSocket):
    await websocket.accept()
    await game_manager.add_lobby_socket(websocket)
    try:
        # Send the initial list to just this client
        await game_manager.send_game_list(websocket)
        while True:
            data = await receive_message(websocket)
            if data.get("type") == "create_game":
                # The creator then connects to /ws/game/{code}; the first to join becomes host.
                code = await game_manager.create_game()
                await websocket.send_json({"type": "game_created", "room_code": code})
    except WebSocketDisconnect:
        pass
    finally:
        await game_manager.remove_lobby_socket(websocket)


@app.websocket("/ws/game/{room_code}/{client_id}")
async def websocket_game(websocket: WebSocket, room_code: str, client_id: str):
    await websocket.accept()

    room_code = room_code.upper()
    game = game_manager.get_game(room_code)
    if not game:
        await websocket.close(code=CLOSE_GAME_NOT_FOUND, reason="Game not found or has ended.")
        return

    try:
        while True:
            data = await receive_message(websocket)
            action = data.get("action")
            lobby_changed = False
            error = None

            async with game.lock:
                if action == "join":
                    ok, reason = game.can_join(client_id)
                    if not ok:
                        await websocket.close(code=CLOSE_GAME_IN_PROGRESS, reason=reason)
                        return
                    existing = game.players.get(client_id)
                    old_ws = existing.websocket if existing else None
                    game.add_player(client_id, sanitize_nickname(data.get("nickname")), websocket)
                    if old_ws is not None and old_ws is not websocket:
                        try:
                            await old_ws.close(code=CLOSE_REPLACED, reason="Opened in another tab.")
                        except Exception:
                            pass
                    await game.broadcast_state()
                    lobby_changed = True

                elif game.players.get(client_id) is None or game.players[client_id].websocket is not websocket:
                    # Not joined (or superseded by another tab) - ignore
                    continue

                elif action == "start_game":
                    error = await game.start_game(client_id)
                    lobby_changed = True

                elif action == "start_voting":
                    error = await game.start_voting(client_id)

                elif action == "vote":
                    error = await game.cast_vote(client_id, data.get("target_id"))

                elif action == "force_resolve":
                    error = await game.force_resolve(client_id)

                elif action == "next_round":
                    error = await game.next_round(client_id)

                elif action == "return_to_lobby":
                    error = await game.return_to_lobby(client_id)
                    lobby_changed = True

                elif action == "set_mystery_mode":
                    error = await game.set_mystery_mode(client_id, data.get("enabled", False))
                    lobby_changed = True

                elif action == "reveal_imposter":
                    # Revealed only for this specific player (not broadcast to others)
                    if game.reveal_imposter(client_id):
                        await websocket.send_json(game.state_for(game.players[client_id]))

                if error:
                    await websocket.send_json({"type": "error", "message": error})

            if lobby_changed:
                await game_manager.broadcast_lobby_update()

    except WebSocketDisconnect:
        pass
    finally:
        changed = False
        async with game.lock:
            changed = await game.handle_disconnect(client_id, websocket)
            if changed:
                await game.broadcast_state()
        if changed:
            if not game.connected_players():
                # Give everyone a grace period to refresh / reconnect before the room is removed
                game_manager.schedule_cleanup(room_code)
            await game_manager.broadcast_lobby_update()
