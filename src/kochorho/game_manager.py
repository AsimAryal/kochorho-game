import random
import string
import asyncio
from typing import Dict, List, Optional
from enum import Enum
from dataclasses import dataclass, field

# --- Data Models & Constants ---

class GameState(Enum):
    LOBBY = "LOBBY"
    ROUND_START = "ROUND_START"  # Revealing words
    VOTING = "VOTING"
    RESULTS = "RESULTS" # Show who was eliminated
    GAME_OVER = "GAME_OVER"

@dataclass
class Player:
    client_id: str
    nickname: str
    is_host: bool = False
    is_imposter: bool = False
    is_alive: bool = True
    websocket: any = None  # WebSocket object
    voted_for: Optional[str] = None  # client_id of who they voted for
    has_seen_imposter: bool = False  # Mystery Mode: has this player revealed the imposter for themselves?

    def to_dict(self):
        return {
            "client_id": self.client_id,
            "nickname": self.nickname,
            "is_host": self.is_host,
            "is_alive": self.is_alive,
            "voted_for": self.voted_for
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
            ("King", "Prince")
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
    def __init__(self, room_code: str, host_id: str, host_name: str, host_ws):
        self.room_code = room_code
        self.players: Dict[str, Player] = {}
        self.state = GameState.LOBBY
        self.word_bank = WordBank()
        
        # Game Settings
        self.mystery_mode: bool = False  # If True, players don't see their role
        
        # Round State
        self.current_majority_word = ""
        self.current_imposter_word = ""
        self.imposter_id: Optional[str] = None
        self.winner: Optional[str] = None  # "Civilians" or "Imposter"

        # Add Host
        self.add_player(host_id, host_name, host_ws, is_host=True)

    def add_player(self, client_id: str, nickname: str, websocket, is_host: bool = False):
        if client_id in self.players:
            # Reconnection logic could go here, for now just update WS
            self.players[client_id].websocket = websocket
            # If name changed? 
            self.players[client_id].nickname = nickname 
        else:
            self.players[client_id] = Player(
                client_id=client_id, 
                nickname=nickname, 
                websocket=websocket, 
                is_host=is_host
            )

    def remove_player(self, client_id: str):
        if client_id not in self.players:
            return
        
        was_host = self.players[client_id].is_host
        del self.players[client_id]
        
        # Host migration: assign new host to first remaining player
        if was_host and self.players:
            first_player_id = list(self.players.keys())[0]
            self.players[first_player_id].is_host = True
    
    async def broadcast_state(self):
        """Sends the current full state to all connected clients."""
        state_data = self.get_state_data()
        for p in self.players.values():
            if p.websocket:
                try:
                    # Customize data for sender (e.g. show words only if round active)
                    player_specific_data = self.enrich_state_for_player(state_data, p)
                    await p.websocket.send_json(player_specific_data)
                except Exception:
                    pass # Handle disconnects elsewhere

    def enrich_state_for_player(self, base_data, player: Player):
        """Adds private info like 'your_word' based on role."""
        data = base_data.copy()
        data["my_id"] = player.client_id
        
        if self.state in [GameState.ROUND_START, GameState.VOTING, GameState.RESULTS]:
            if player.is_alive:
                if player.is_imposter:
                    data["my_word"] = self.current_imposter_word
                    # In Mystery Mode, don't reveal role to alive players
                    if self.mystery_mode:
                        data["my_role"] = "???"
                    else:
                        data["my_role"] = "Imposter"
                else:
                    data["my_word"] = self.current_majority_word
                    if self.mystery_mode:
                        data["my_role"] = "???"
                    else:
                        data["my_role"] = "Civilian"
            else:
                # Eliminated players
                data["my_word"] = "ELIMINATED"
                data["my_role"] = "Spectator"
                # In Mystery Mode, eliminated players can reveal imposter for themselves
                if self.mystery_mode:
                    if player.has_seen_imposter:
                        # Player has already revealed - show them the imposter
                        data["imposter_identity"] = self.get_player_name(self.imposter_id)
                        data["has_revealed"] = True
                    else:
                        # Show "Reveal" button option
                        data["can_reveal_imposter"] = True
        
        # In game over, reveal everything to everyone
        if self.state == GameState.GAME_OVER:
            data["imposter_identity"] = self.get_player_name(self.imposter_id)

        return data

    def get_state_data(self):
        return {
            "room_code": self.room_code,
            "state": self.state.value,
            "players": [p.to_dict() for p in self.players.values()],
            "winner": self.winner,
            "mystery_mode": self.mystery_mode
        }

    def get_player_name(self, client_id):
        return self.players[client_id].nickname if client_id in self.players else "Unknown"

    # --- Actions ---

    async def start_game(self):
        if len(self.players) < 3:
            return False, "Need at least 3 players."
        
        # Assign Roles (Persists for whole game)
        player_ids = list(self.players.keys())
        self.imposter_id = random.choice(player_ids)
        
        for pid, p in self.players.items():
            p.is_imposter = (pid == self.imposter_id)
            p.is_alive = True
            p.voted_for = None
            
        self.winner = None
        self.word_bank.used_indices = set() # Reset word bank usage for new game? Or keep history?
        # Requirement: "Imposter person remains the same". 
        # Requirement: "A new Word Pair is chosen every round".
        
        await self.start_round()
        return True, "Game Started"

    async def start_round(self):
        majority, imposter = self.word_bank.get_pair()
        self.current_majority_word = majority
        self.current_imposter_word = imposter
        
        # Reset votes logic if needed (votes are per round)
        for p in self.players.values():
            p.voted_for = None
            
        self.state = GameState.ROUND_START
        await self.broadcast_state()

    async def start_voting(self):
        self.state = GameState.VOTING
        await self.broadcast_state()

    async def cast_vote(self, voter_id: str, target_id: str):
        if self.state != GameState.VOTING:
            return
        
        voter = self.players.get(voter_id)
        if not voter or not voter.is_alive:
            return

        voter.voted_for = target_id
        await self.broadcast_state()

        # Check if everyone voted
        alive_players = [p for p in self.players.values() if p.is_alive]
        total_votes = sum(1 for p in alive_players if p.voted_for is not None)
        
        if total_votes == len(alive_players):
            await self.resolve_votes()

    async def resolve_votes(self):
        # Tally votes
        vote_counts = {}
        for p in self.players.values():
            if p.is_alive and p.voted_for:
                vote_counts[p.voted_for] = vote_counts.get(p.voted_for, 0) + 1
        
        if not vote_counts:
            # Should not happen if we waited for all votes
            return

        # Find max votes
        max_votes = max(vote_counts.values())
        candidates = [pid for pid, count in vote_counts.items() if count == max_votes]
        
        eliminated_id = None
        
        if len(candidates) == 1:
            # Strict majority
            eliminated_id = candidates[0]
            self.players[eliminated_id].is_alive = False
        else:
            # Tie - No elimination per plan
            pass 

        self.state = GameState.RESULTS
        await self.broadcast_state()
        
        # Check Win Conditions
        await self.check_win_condition(eliminated_id)

    async def check_win_condition(self, eliminated_id):
        # 1. Imposter Eliminated -> Civilians Win
        if eliminated_id and eliminated_id == self.imposter_id:
            self.winner = "Civilians"
            self.state = GameState.GAME_OVER
            await self.broadcast_state()
            return

        # 2. Imposter Survives & 1:1 Ratio -> Imposter Wins
        alive_players = [p for p in self.players.values() if p.is_alive]
        alive_count = len(alive_players)
        
        # If imposter is still alive (implicit, otherwise caught above)
        # Ratio 1 Imposter vs 1 Civilian = 2 players total
        if alive_count <= 2:
            self.winner = "Imposter"
            self.state = GameState.GAME_OVER
            await self.broadcast_state()
            return
            
        # Continue Game if no win
        # Wait a bit then next round? Or manual trigger? 
        # Usually automated or Host clicks "Next Round". 
        # Let's make it Host triggered for better pacing, OR auto after 5s.
        # For this MVP, let's keep it manual "Next Round" button for Host in RESULTS screen.

    async def next_round(self):
        # Proceed to next round with new words
        if self.state == GameState.RESULTS:
            await self.start_round()

# --- Manager ---

class GameManager:
    def __init__(self):
        self.games: Dict[str, GameInstance] = {}
        self.lobby_sockets = set()

    def generate_code(self):
        while True:
            code = ''.join(random.choices(string.ascii_uppercase, k=4))
            if code not in self.games:
                return code

    async def create_game(self, host_id: str, host_name: str, host_ws) -> str:
        code = self.generate_code()
        game = GameInstance(code, host_id, host_name, host_ws)
        self.games[code] = game
        await self.broadcast_lobby_update()
        return code

    def get_game(self, room_code: str) -> Optional[GameInstance]:
        return self.games.get(room_code)

    async def add_lobby_socket(self, ws):
        self.lobby_sockets.add(ws)
    
    async def remove_lobby_socket(self, ws):
        self.lobby_sockets.discard(ws)

    async def broadcast_lobby_update(self):
        """Sends list of active games to lobby waiters."""
        game_list = [
            {
                "code": g.room_code,
                "host": g.players[list(g.players.keys())[0]].nickname, # Use first player as host roughly
                "count": len(g.players),
                "state": g.state.value
            }
            for g in self.games.values()
            if g.state == GameState.LOBBY # Only show joinable games? Or all? Plan said "Live List" 
        ]
        
        to_remove = []
        for ws in self.lobby_sockets:
            try:
                await ws.send_json({"type": "gamelist", "games": game_list})
            except Exception:
                to_remove.append(ws)
        
        for ws in to_remove:
            self.lobby_sockets.discard(ws)

game_manager = GameManager()
