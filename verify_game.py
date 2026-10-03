"""End-to-end smoke test. Start the server first:

    uv run uvicorn kochorho.main:app --app-dir src --port 8001

then run:  uv run python verify_game.py
"""
import asyncio
import json
import sys

import websockets

URI = "ws://localhost:8001"


class Client:
    """A fake player that keeps the most recent game state it received."""

    def __init__(self, cid: str, name: str):
        self.cid, self.name = cid, name
        self.ws = None
        self.state = None
        self.errors = []
        self._reader = None
        self._updated = asyncio.Event()

    async def join(self, code: str):
        self.state = None  # Don't trust anything from a previous connection
        self.ws = await websockets.connect(f"{URI}/ws/game/{code}/{self.cid}")
        self._reader = asyncio.create_task(self._read())
        await self.send("join", nickname=self.name)
        await self.wait_for(lambda s: any(p["client_id"] == self.cid for p in s["players"]))

    async def _read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "error":
                    self.errors.append(msg["message"])
                else:
                    self.state = msg
                self._updated.set()
        except websockets.ConnectionClosed:
            pass

    async def send(self, action: str, **payload):
        await self.ws.send(json.dumps({"action": action, **payload}))

    async def wait_for(self, predicate, timeout: float = 3.0):
        async def _loop():
            while not (self.state and predicate(self.state)):
                self._updated.clear()
                await self._updated.wait()
            return self.state
        return await asyncio.wait_for(_loop(), timeout)

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self._reader:
            await asyncio.gather(self._reader, return_exceptions=True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


async def create_room() -> str:
    async with websockets.connect(f"{URI}/ws/lobby") as lobby:
        await lobby.send(json.dumps({"type": "create_game"}))
        while True:
            data = json.loads(await lobby.recv())
            if data.get("type") == "game_created":
                return data["room_code"]


async def run_test():
    code = await create_room()
    print(f"Room {code}")

    host = Client("host1", "Host")
    p2 = Client("p2", "<img src=x onerror=alert(1)> very long name")
    p3 = Client("p3", "Player3")
    clients = [host, p2, p3]

    await host.join(code)
    check(host.state["players"][0]["is_host"], "first player to join becomes host")
    await p2.join(code)
    await p3.join(code)
    await host.wait_for(lambda s: len(s["players"]) == 3)
    check(len(next(p for p in host.state["players"] if p["client_id"] == "p2")["nickname"]) <= 16,
          "nicknames are length-capped")

    # Only the host can start
    await p2.send("start_game")
    await asyncio.sleep(0.2)
    check(p2.state["state"] == "LOBBY" and p2.errors, "non-host cannot start the game")

    await host.send("start_game")
    for c in clients:
        await c.wait_for(lambda s: s["state"] == "ROUND_START")
    roles = {c.cid: c.state["my_role"] for c in clients}
    check(list(roles.values()).count("Imposter") == 1, "exactly one imposter assigned")
    imposter = next(c for c in clients if roles[c.cid] == "Imposter")
    civilians = [c for c in clients if c is not imposter]
    word_before = imposter.state["my_word"]

    # Late joiners are turned away mid-game
    late = await websockets.connect(f"{URI}/ws/game/{code}/late")
    await late.send(json.dumps({"action": "join", "nickname": "Late"}))
    try:
        await asyncio.wait_for(late.recv(), 2)
        check(False, "late joiner should be rejected")
    except websockets.ConnectionClosed as e:
        check(e.rcvd.code == 4001, "late joiner rejected with 4001")

    # Imposter drops (phone locks) and comes back - must keep their role and word
    await imposter.close()
    await civilians[0].wait_for(lambda s: any(p["client_id"] == imposter.cid and not p["is_connected"] for p in s["players"]))
    check(True, "disconnected player keeps their seat mid-game")
    await imposter.join(code)
    await imposter.wait_for(lambda s: s["state"] == "ROUND_START")
    check(imposter.state["my_role"] == "Imposter" and imposter.state["my_word"] == word_before,
          "reconnecting imposter keeps role and word")

    # Host duties may have migrated if the host was the one who dropped
    host_row = next(p for p in imposter.state["players"] if p["is_host"])
    check(host_row["is_connected"], "host role is always held by an online player")
    host = next(c for c in clients if c.cid == host_row["client_id"])

    # Voting
    await host.send("start_voting")
    for c in clients:
        await c.wait_for(lambda s: s["state"] == "VOTING")
    check(all("voted_for" not in p for p in host.state["players"]), "individual votes are private")

    await civilians[0].send("vote", target_id=civilians[0].cid)  # self-vote is invalid
    await asyncio.sleep(0.2)
    check(civilians[0].errors, "self-votes are rejected")

    for c in clients:
        target = imposter.cid if c is not imposter else civilians[0].cid
        await c.send("vote", target_id=target)

    final = await host.wait_for(lambda s: s["state"] == "GAME_OVER")
    check(final["winner"] == "Civilians", "voting out the imposter -> Civilians win")
    check(final["last_result"]["eliminated_id"] == imposter.cid, "results report who was eliminated")
    check(final["words"]["imposter"] == word_before, "both words revealed at game over")

    # Play again keeps the room together
    await host.send("return_to_lobby")
    for c in clients:
        await c.wait_for(lambda s: s["state"] == "LOBBY")
    check(all(p["is_alive"] for p in host.state["players"]), "play again resets the room to the lobby")

    for c in clients:
        await c.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_test())
        print("All checks passed")
    except Exception as e:
        print(f"Test Failed: {type(e).__name__}: {e}")
        sys.exit(1)
