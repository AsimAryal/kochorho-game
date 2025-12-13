import asyncio
import websockets
import json
import sys

# Constants
URI = "ws://localhost:8001"

async def connect_client(name):
    """Simulates a client connecting to lobby and then game."""
    try:
        async with websockets.connect(f"{URI}/ws/lobby") as ws_lobby:
            print(f"[{name}] Connected to Lobby")
            return ws_lobby
    except Exception as e:
        print(f"[{name}] Failed to connect: {e}")
        return None

async def run_test():
    async with websockets.connect(f"{URI}/ws/lobby") as ws_lobby_host:
        print("[Host] Connected to Lobby")
        
        # 1. Create Game
        host_id = "host1"
        await ws_lobby_host.send(json.dumps({
            "type": "create_game", 
            "client_id": host_id, 
            "nickname": "Host"
        }))
        
        while True:
            resp = await ws_lobby_host.recv()
            data = json.loads(resp)
            if data.get("type") == "game_created":
                break
            if data.get("type") == "gamelist":
                continue # Ignore updates
            
        room_code = data["room_code"]
        print(f"[Host] Game Created: {room_code}")
        
        # 2. Connect Players to Game
        players = []
        for i, role in enumerate(["Host", "Player2", "Player3"]):
             pid = f"p{i}"
             ws = await websockets.connect(f"{URI}/ws/game/{room_code}/{pid}")
             await ws.send(json.dumps({"action": "join", "nickname": role}))
             players.append({"ws": ws, "id": pid, "name": role, "role": None})
             print(f"[{role}] Joined Game")
             
             # Consume initial state
             state = json.loads(await ws.recv())
             # print(f"[{role}] State: {state['state']}")

        # 3. Start Game
        print("[Host] Starting Game...")
        await players[0]["ws"].send(json.dumps({"action": "start_game"}))
        
        # Verify State chagne to ROUND_START
        imposter_found = False
        civilian_found = False
        
        for p in players:
             while True:
                 state = json.loads(await p["ws"].recv())
                 print(f"[{p['name']}] Received State: {state['state']}")
                 if state['state'] == "ROUND_START":
                     break
                 # If we get LOBBY, just continue (stale)
                 
             if state['my_role'] == 'Imposter': imposter_found = True
             if state['my_role'] == 'Civilian': civilian_found = True
             p["role"] = state["my_role"]
             print(f"   -> Role: {state['my_role']}, Word: {state['my_word']}")
        
        if not (imposter_found and civilian_found):
             print("ERROR: Roles not assigned correctly!")
             return

        # 4. Start Voting
        print("[Host] Starting Voting Phase...")
        await players[0]["ws"].send(json.dumps({"action": "start_voting"}))
        
        for p in players:
            state = json.loads(await p["ws"].recv())
            # print(f"[{p['name']}] Voting State: {state['state']}")

        # 5. Vote (Vote out Player 2 arbitrarily)
        target = players[1]["id"] 
        print(f"Voting for {players[1]['name']} ({target})...")
        
        for p in players:
            await p["ws"].send(json.dumps({"action": "vote", "target_id": target}))
            # Consume broadcast after each vote
            # Wait, 3 votes means 3 broadcasts? 
            # Logic: cast_vote -> broadcast.
            # So each player receives updates. We might get multiple.
            # Just drain queue slightly or wait for final result?
            pass

        # Check Results
        # Eventually we should get state RESULTS
        await asyncio.sleep(0.5)
        # Flush messages
        final_state = None
        for p in players:
            while True:
                try:
                    msg = await asyncio.wait_for(p["ws"].recv(), timeout=0.2)
                    state = json.loads(msg)
                    if state['state'] == 'RESULTS' or state['state'] == 'GAME_OVER':
                        final_state = state
                except asyncio.TimeoutError:
                    break
        
        if final_state:
            print(f"Final State: {final_state['state']}")
            print(f"Winner: {final_state.get('winner')}")
        else:
            print("Did not reach RESULTS state.")

if __name__ == "__main__":
    try:
        asyncio.run(run_test())
        print("Test Complete")
    except Exception as e:
        print(f"Test Failed: {e}")
