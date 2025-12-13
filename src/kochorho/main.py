from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import pathlib
import json

from .game_manager import game_manager, GameState

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

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- WebSockets ---

@app.websocket("/ws/lobby")
async def websocket_lobby(websocket: WebSocket):
    await websocket.accept()
    await game_manager.add_lobby_socket(websocket)
    try:
        # Send initial list
        await game_manager.broadcast_lobby_update()
        while True:
            # Keep connection open, ignore incoming messages from lobby for now
            # or handle "create_game" here if we wanted pure WS, but API is fine too.
            # Actually, let's use WS for create_game to keep it all in one pipe?
            # User requirement: "Route/Websocket to fetch or stream List...". 
            # I'll stick to receiving commands via this socket for creating/joining? 
            # Or mix? Let's generic message loop.
            
            data = await websocket.receive_json()
            command = data.get("type")
            
            if command == "create_game":
                 host_id = data.get("client_id")
                 host_name = data.get("nickname")
                 # We need to upgrade this socket to a game socket? 
                 # Or just return the code and let client reconnect to /ws/game?
                 # Reconnecting is cleaner for separation of concerns.
                 code = await game_manager.create_game(host_id, host_name, None) # WS added later
                 await websocket.send_json({"type": "game_created", "room_code": code})
                 
    except WebSocketDisconnect:
        await game_manager.remove_lobby_socket(websocket)

@app.websocket("/ws/game/{room_code}/{client_id}")
async def websocket_game(websocket: WebSocket, room_code: str, client_id: str):
    await websocket.accept()
    
    game = game_manager.get_game(room_code)
    if not game:
        await websocket.close(code=4000, reason="Game not found")
        return

    # Add player to game instance (or update connection)
    # We need nickname. We can send it in the first message or query param. 
    # Let's assume client sends "join" message immediately after connect.
    
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "join":
                nickname = data.get("nickname")
                game.add_player(client_id, nickname, websocket)
                await game.broadcast_state()
            
            elif action == "start_game":
                success, msg = await game.start_game()
                # Could send error if fail

            elif action == "start_voting":
                await game.start_voting()
                
            elif action == "next_round":
                await game.next_round()

            elif action == "vote":
                target_id = data.get("target_id")
                await game.cast_vote(client_id, target_id)
            
            elif action == "set_mystery_mode":
                # Only host can toggle, only in lobby
                player = game.players.get(client_id)
                if player and player.is_host and game.state == GameState.LOBBY:
                    game.mystery_mode = data.get("enabled", False)
                    await game.broadcast_state()
            
            elif action == "reveal_imposter":
                # Revealed only for this specific player (not broadcast to others)
                player = game.players.get(client_id)
                if player and not player.is_alive and game.mystery_mode and not player.has_seen_imposter:
                    player.has_seen_imposter = True
                    # Only send updated state to this player, not broadcast to everyone
                    player_data = game.enrich_state_for_player(game.get_state_data(), player)
                    await websocket.send_json(player_data)
                
    except WebSocketDisconnect:
        game.remove_player(client_id)
        # Cleanup empty games and update lobby
        if not game.players:
            if room_code in game_manager.games:
                del game_manager.games[room_code]
            await game_manager.broadcast_lobby_update()
        else:
            await game.broadcast_state()
            # Update lobby if game state changed (e.g., player count)
            await game_manager.broadcast_lobby_update()
