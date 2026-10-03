import random
import asyncio
import logging
from typing import Any, Dict, List, Optional
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- Data Models & Constants ---

MIN_PLAYERS = 3
MAX_NICKNAME_LENGTH = 16
EMPTY_GAME_GRACE_SECONDS = 60  # How long an empty room survives (lets people refresh / reconnect)
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # No I / O to avoid confusion with 1 / 0


class GameState(Enum):
    LOBBY = "LOBBY"
    ROUND_START = "ROUND_START"  # Revealing words
    VOTING = "VOTING"
    RESULTS = "RESULTS" # Show who was eliminated
    GAME_OVER = "GAME_OVER"


ACTIVE_ROUND_STATES = (GameState.ROUND_START, GameState.VOTING, GameState.RESULTS)


def sanitize_nickname(raw: Any) -> str:
    """Trim, collapse whitespace and cap the length of a nickname."""
    name = " ".join(str(raw or "").split())[:MAX_NICKNAME_LENGTH]
    return name or "Player"


@dataclass
class Player:
    client_id: str
    nickname: str
    is_host: bool = False
    is_imposter: bool = False
    is_alive: bool = True
    websocket: Any = None  # WebSocket object (None while disconnected)
    voted_for: Optional[str] = None  # client_id of who they voted for
    has_seen_imposter: bool = False  # Mystery Mode: has this player revealed the imposter for themselves?

    @property
    def is_connected(self) -> bool:
        return self.websocket is not None

    def to_dict(self):
        # NOTE: who voted for whom stays private until the results are in.
        return {
            "client_id": self.client_id,
            "nickname": self.nickname,
            "is_host": self.is_host,
            "is_alive": self.is_alive,
            "is_connected": self.is_connected,
            "has_voted": self.voted_for is not None,
        }


class WordBank:
    """Manages the dictionary of word pairs."""
    def __init__(self):
        # Format: (Majority Word, Imposter Word)
        self.pairs = [
            ("Apple", "Pear"),
            ("Car", "Truck"),
            ("Coffee", "Tea"),
            ("Dog", "Wolf"),
            ("Sun", "Moon"),
            ("Ocean", "Lake"),
            ("Piano", "Guitar"),
            ("Superman", "Batman"),
            ("Pizza", "Burger"),
            ("Pen", "Pencil"),
            ("Chair", "Stool"),
            ("Shoes", "Socks"),
            ("Train", "Bus"),
            ("Lion", "Tiger"),
            ("Gold", "Silver"),
            ("Book", "Magazine"),
            ("Facebook", "Twitter"),
            ("iPhone", "Android"),
            ("Doctor", "Nurse"),
            ("King", "Prince"),
            ("Beach", "Desert"),
            ("Snow", "Rain"),
            ("Cat", "Fox"),
            ("Football", "Rugby"),
            ("Tennis", "Badminton"),
            ("Wine", "Beer"),
            ("Chocolate", "Caramel"),
            ("Pancake", "Waffle"),
            ("Sushi", "Ramen"),
            ("Mountain", "Volcano"),
            ("River", "Waterfall"),
            ("Castle", "Palace"),
            ("Pirate", "Ninja"),
            ("Vampire", "Zombie"),
            ("Wizard", "Witch"),
            ("Dragon", "Dinosaur"),
            ("Rocket", "Aeroplane"),
            ("Bicycle", "Scooter"),
            ("Hospital", "Pharmacy"),
            ("Library", "Bookshop"),
            ("Cinema", "Theatre"),
            ("Netflix", "YouTube"),
            ("Instagram", "TikTok"),
            ("Laptop", "Tablet"),
            ("Camera", "Telescope"),
            ("Umbrella", "Raincoat"),
            ("Pillow", "Blanket"),
            ("Shower", "Bath"),
            ("Fork", "Spoon"),
            ("Salt", "Sugar"),
            ("Ketchup", "Mustard"),
            ("Orange", "Lemon"),
            ("Strawberry", "Cherry"),
            ("Banana", "Mango"),
            ("Ice Cream", "Frozen Yoghurt"),
            ("Birthday", "Wedding"),
            ("Christmas", "Halloween"),
            ("Teacher", "Professor"),
            ("Police", "Firefighter"),
            ("Chef", "Waiter"),
            ("Dentist", "Barber"),
            ("Shark", "Dolphin"),
            ("Eagle", "Owl"),
            ("Bee", "Wasp"),
            ("Spider", "Scorpion"),
            ("Rose", "Tulip"),
            ("Guitar", "Violin"),
            ("Drum", "Tambourine"),
            ("Harry Potter", "Lord of the Rings"),
            ("Mario", "Sonic"),
            ("Chess", "Draughts"),
            ("Karaoke", "Disco"),
            ("Gym", "Yoga"),
            ("Passport", "Ticket"),
            ("Hotel", "Hostel"),
            ("Diamond", "Pearl"),
            ("Clock", "Watch"),
            ("Mirror", "Window"),
            ("Candle", "Torch"),
            ("Ghost", "Alien"),
        ]
        self.used_indices = set()

    def get_pair(self) -> tuple[str, str]:
        """Returns a random unused pair (Majority, Imposter). Resets if all used.
        Word order is randomized to prevent predictability."""
        available_indices = set(range(len(self.pairs))) - self.used_indices
        if not available_indices:
            self.used_indices = set()
            available_indices = set(range(len(self.pairs)))

        idx = random.choice(list(available_indices))
        self.used_indices.add(idx)
        pair = self.pairs[idx]
        # Randomly swap order so imposter doesn't always get same position
        if random.choice([True, False]):
            return (pair[1], pair[0])
        return pair

# --- Game Logic ---

class GameInstance:
    def __init__(self, room_code: str):
        self.room_code = room_code
        self.players: Dict[str, Player] = {}
        self.state = GameState.LOBBY
        self.word_bank = WordBank()
        # Serialises all actions for this room so concurrent messages can't interleave mid-update.
        self.lock = asyncio.Lock()

        # Game Settings
        self.mystery_mode: bool = False  # If True, players don't see their role

        # Round State
        self.round_number = 0
        self.current_majority_word = ""
        self.current_imposter_word = ""
        self.imposter_id: Optional[str] = None
        self.winner: Optional[str] = None  # "Civilians" or "Imposter"
        self.last_result: Optional[dict] = None  # Outcome of the most recent vote

    # --- Player helpers ---

    def connected_players(self) -> List[Player]:
        return [p for p in self.players.values() if p.is_connected]

    def alive_players(self) -> List[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def host(self) -> Optional[Player]:
        return next((p for p in self.players.values() if p.is_host), None)

    def is_host(self, client_id: str) -> bool:
        p = self.players.get(client_id)
        return bool(p and p.is_host)

    def get_player_name(self, client_id):
        return self.players[client_id].nickname if client_id in self.players else "Unknown"

    def ensure_host(self):
        """Make sure a *connected* player holds the host role, so the game never stalls."""
        current = self.host()
        if current and current.is_connected:
            return
        candidate = next((p for p in self.players.values() if p.is_connected), None)
        if candidate is None:
            return  # Nobody online; keep the current host flag until someone returns
        if current:
            current.is_host = False
        candidate.is_host = True

    def can_join(self, client_id: str) -> tuple[bool, str]:
        if client_id in self.players:
            return True, ""  # Reconnecting player keeps their seat
        if self.state == GameState.LOBBY:
            return True, ""
        return False, "That game is already in progress."

    def add_player(self, client_id: str, nickname: str, websocket):
        """Adds a new player, or re-attaches a returning player to a new socket."""
        player = self.players.get(client_id)
        if player:
            player.websocket = websocket
            player.nickname = nickname
        else:
            self.players[client_id] = Player(
                client_id=client_id,
                nickname=nickname,
                websocket=websocket,
            )
        self.ensure_host()

    async def handle_disconnect(self, client_id: str, websocket) -> bool:
        """Called when a socket closes. Returns True if the game state changed."""
        player = self.players.get(client_id)
        # Ignore stale sockets (e.g. the player already reconnected from a refreshed tab)
        if not player or player.websocket is not websocket:
            return False

        if self.state == GameState.LOBBY:
            del self.players[client_id]
        else:
            # Mid-game we keep their seat (role, alive status, vote) so they can come back.
            player.websocket = None

        self.ensure_host()
        await self.maybe_resolve_votes()
        return True

    # --- State serialisation ---

    async def broadcast_state(self):
        """Sends the current full state to all connected clients."""
        state_data = self.get_state_data()
        for p in list(self.players.values()):
            if p.websocket:
                try:
                    await p.websocket.send_json(self.enrich_state_for_player(state_data, p))
                except Exception:
                    pass  # Disconnects are handled by the socket's own handler

    def state_for(self, player: Player) -> dict:
        return self.enrich_state_for_player(self.get_state_data(), player)

    def enrich_state_for_player(self, base_data, player: Player):
        """Adds private info like 'my_word' based on role."""
        data = dict(base_data)
        data["type"] = "state"
        data["my_id"] = player.client_id

        if self.state in ACTIVE_ROUND_STATES:
            data["my_vote"] = player.voted_for
            if player.is_alive:
                if player.is_imposter:
                    data["my_word"] = self.current_imposter_word
                    real_role = "Imposter"
                else:
                    data["my_word"] = self.current_majority_word
                    real_role = "Civilian"
                # In Mystery Mode, don't reveal role to alive players
                data["my_role"] = "???" if self.mystery_mode else real_role
            else:
                # Eliminated players
                data["my_word"] = None
                data["my_role"] = "Spectator"
                # In Mystery Mode, eliminated players can reveal imposter for themselves
                if self.mystery_mode:
                    if player.has_seen_imposter:
                        data["imposter_identity"] = self.get_player_name(self.imposter_id)
                        data["imposter_id"] = self.imposter_id
                        data["has_revealed"] = True
                    else:
                        data["can_reveal_imposter"] = True

        # In game over, reveal everything to everyone
        if self.state == GameState.GAME_OVER:
            data["imposter_identity"] = self.get_player_name(self.imposter_id)
            data["imposter_id"] = self.imposter_id
            data["my_role"] = "Imposter" if player.is_imposter else "Civilian"
            data["words"] = {
                "civilian": self.current_majority_word,
                "imposter": self.current_imposter_word,
            }

        return data

    def get_state_data(self):
        data = {
            "room_code": self.room_code,
            "state": self.state.value,
            "players": [p.to_dict() for p in self.players.values()],
            "winner": self.winner,
            "mystery_mode": self.mystery_mode,
            "round_number": self.round_number,
            "min_players": MIN_PLAYERS,
        }
        if self.state == GameState.VOTING:
            voters = self._eligible_voters()
            data["votes_cast"] = sum(1 for p in voters if p.voted_for)
            data["votes_needed"] = len(voters)
        if self.state in (GameState.RESULTS, GameState.GAME_OVER):
            data["last_result"] = self.last_result
        return data

    # --- Actions (each returns an error message, or None on success) ---

    async def start_game(self, actor_id: str) -> Optional[str]:
        if not self.is_host(actor_id):
            return "Only the host can start the game."
        if self.state != GameState.LOBBY:
            return "The game has already started."
        candidates = self.connected_players()
        if len(candidates) < MIN_PLAYERS:
            return f"You need at least {MIN_PLAYERS} players to start."

        # Assign Roles (Persists for whole game)
        self.imposter_id = random.choice([p.client_id for p in candidates])
        for pid, p in self.players.items():
            p.is_imposter = (pid == self.imposter_id)
            p.is_alive = True
            p.voted_for = None
            p.has_seen_imposter = False

        self.winner = None
        self.round_number = 0
        self.last_result = None
        # Word bank history is kept across games in the same room so pairs don't repeat;
        # it resets itself once every pair has been used.
        await self.start_round()
        return None

    async def start_round(self):
        majority, imposter = self.word_bank.get_pair()
        self.current_majority_word = majority
        self.current_imposter_word = imposter
        self.round_number += 1
        self.last_result = None

        # Votes are per round
        for p in self.players.values():
            p.voted_for = None

        self.state = GameState.ROUND_START
        await self.broadcast_state()

    async def start_voting(self, actor_id: str) -> Optional[str]:
        if not self.is_host(actor_id):
            return "Only the host can start voting."
        if self.state != GameState.ROUND_START:
            return None
        self.state = GameState.VOTING
        await self.broadcast_state()
        return None

    def _eligible_voters(self) -> List[Player]:
        """Alive players who are currently online; we don't wait on people who dropped out."""
        return [p for p in self.alive_players() if p.is_connected]

    async def cast_vote(self, voter_id: str, target_id: Any) -> Optional[str]:
        if self.state != GameState.VOTING:
            return None

        voter = self.players.get(voter_id)
        if not voter or not voter.is_alive:
            return "Eliminated players can't vote."

        target = self.players.get(target_id) if isinstance(target_id, str) else None
        if not target or not target.is_alive or target_id == voter_id:
            return "That's not a valid vote."

        voter.voted_for = target_id
        await self.broadcast_state()
        await self.maybe_resolve_votes()
        return None

    async def maybe_resolve_votes(self):
        """Resolve once every online, alive player has voted."""
        if self.state != GameState.VOTING:
            return
        voters = self._eligible_voters()
        if voters and all(p.voted_for for p in voters):
            await self.resolve_votes()

    async def force_resolve(self, actor_id: str) -> Optional[str]:
        """Host escape hatch for an AFK player holding up the vote."""
        if not self.is_host(actor_id):
            return "Only the host can close voting."
        if self.state != GameState.VOTING:
            return None
        if not any(p.voted_for for p in self.alive_players()):
            return "Nobody has voted yet."
        await self.resolve_votes()
        return None

    async def resolve_votes(self):
        # Tally votes (only from alive players, only for alive targets)
        vote_counts: Dict[str, int] = {}
        for p in self.alive_players():
            target = self.players.get(p.voted_for) if p.voted_for else None
            if target and target.is_alive:
                vote_counts[target.client_id] = vote_counts.get(target.client_id, 0) + 1

        eliminated_id = None
        if vote_counts:
            max_votes = max(vote_counts.values())
            candidates = [pid for pid, count in vote_counts.items() if count == max_votes]
            if len(candidates) == 1:
                # Strict plurality; a tie means no elimination
                eliminated_id = candidates[0]
                self.players[eliminated_id].is_alive = False

        tally = sorted(
            (
                {"client_id": pid, "nickname": self.get_player_name(pid), "votes": count}
                for pid, count in vote_counts.items()
            ),
            key=lambda row: row["votes"],
            reverse=True,
        )
        self.last_result = {
            "eliminated_id": eliminated_id,
            "eliminated_name": self.get_player_name(eliminated_id) if eliminated_id else None,
            "tie": eliminated_id is None,
            "tally": tally,
        }

        # Check win conditions *before* broadcasting so clients get a single, final update.
        self.winner = self._compute_winner(eliminated_id)
        self.state = GameState.GAME_OVER if self.winner else GameState.RESULTS
        await self.broadcast_state()

    def _compute_winner(self, eliminated_id: Optional[str]) -> Optional[str]:
        # 1. Imposter Eliminated -> Civilians Win
        if eliminated_id and eliminated_id == self.imposter_id:
            return "Civilians"
        # 2. Imposter Survives & 1:1 Ratio (2 players left) -> Imposter Wins
        if len(self.alive_players()) <= 2:
            return "Imposter"
        # Otherwise the host moves on with "Next Round"
        return None

    async def next_round(self, actor_id: str) -> Optional[str]:
        if not self.is_host(actor_id):
            return "Only the host can start the next round."
        if self.state == GameState.RESULTS:
            await self.start_round()
        return None

    async def return_to_lobby(self, actor_id: str) -> Optional[str]:
        """After a game ends, keep the room together and go back to the waiting room."""
        if not self.is_host(actor_id):
            return "Only the host can start a new game."
        if self.state != GameState.GAME_OVER:
            return None

        # Anyone who left during the game gives up their seat now
        for pid in [pid for pid, p in self.players.items() if not p.is_connected]:
            del self.players[pid]
        for p in self.players.values():
            p.is_imposter = False
            p.is_alive = True
            p.voted_for = None
            p.has_seen_imposter = False

        self.state = GameState.LOBBY
        self.imposter_id = None
        self.winner = None
        self.round_number = 0
        self.last_result = None
        self.ensure_host()
        await self.broadcast_state()
        return None

    async def set_mystery_mode(self, actor_id: str, enabled: Any) -> Optional[str]:
        if not self.is_host(actor_id) or self.state != GameState.LOBBY:
            return None
        self.mystery_mode = bool(enabled)
        await self.broadcast_state()
        return None

    def reveal_imposter(self, actor_id: str) -> bool:
        """Mystery Mode: an eliminated player peeks at the imposter (only for themselves)."""
        player = self.players.get(actor_id)
        if (
            player
            and not player.is_alive
            and self.mystery_mode
            and self.state in ACTIVE_ROUND_STATES
            and not player.has_seen_imposter
        ):
            player.has_seen_imposter = True
            return True
        return False

# --- Manager ---

class GameManager:
    def __init__(self):
        self.games: Dict[str, GameInstance] = {}
        self.lobby_sockets = set()
        self._background_tasks = set()

    def generate_code(self):
        while True:
            code = ''.join(random.choices(ROOM_CODE_ALPHABET, k=4))
            if code not in self.games:
                return code

    async def create_game(self) -> str:
        code = self.generate_code()
        self.games[code] = GameInstance(code)
        # If the creator never actually joins, don't leave a zombie room behind.
        self.schedule_cleanup(code)
        return code

    def get_game(self, room_code: str) -> Optional[GameInstance]:
        return self.games.get((room_code or "").upper())

    def schedule_cleanup(self, room_code: str):
        task = asyncio.create_task(self._cleanup_if_empty(room_code))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _cleanup_if_empty(self, room_code: str):
        await asyncio.sleep(EMPTY_GAME_GRACE_SECONDS)
        game = self.games.get(room_code)
        if game and not game.connected_players():
            del self.games[room_code]
            logger.info("Removed empty room %s", room_code)
            await self.broadcast_lobby_update()

    async def add_lobby_socket(self, ws):
        self.lobby_sockets.add(ws)

    async def remove_lobby_socket(self, ws):
        self.lobby_sockets.discard(ws)

    def public_game_list(self) -> List[dict]:
        """Joinable rooms: waiting in the lobby with at least one person online."""
        games = []
        for g in self.games.values():
            online = g.connected_players()
            if g.state != GameState.LOBBY or not online:
                continue
            host = g.host()
            games.append({
                "code": g.room_code,
                "host": host.nickname if host else online[0].nickname,
                "count": len(online),
                "state": g.state.value,
                "mystery_mode": g.mystery_mode,
            })
        return games

    async def send_game_list(self, ws):
        await ws.send_json({"type": "gamelist", "games": self.public_game_list()})

    async def broadcast_lobby_update(self):
        """Sends list of active games to lobby waiters."""
        message = {"type": "gamelist", "games": self.public_game_list()}
        to_remove = []
        # Iterate over a snapshot: the set can change while we await sends.
        for ws in list(self.lobby_sockets):
            try:
                await ws.send_json(message)
            except Exception:
                to_remove.append(ws)

        for ws in to_remove:
            self.lobby_sockets.discard(ws)

game_manager = GameManager()
