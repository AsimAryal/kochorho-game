# KoChorHo

A real-time multiplayer party game similar to "The Chameleon", built for local network deployment. Perfect for game nights, parties, and social gatherings!

## About the Game

**KoChorHo** is a social deduction party game where players try to identify the imposter among them. Each round:

- **Civilians** receive the same secret word
- The **Imposter** receives a different (but related) word
- Players discuss and give clues about their word, trying to identify who has a different word
- The imposter must blend in by pretending they know the real word
- Players vote to eliminate who they think is the imposter

### Game Modes

- **Classic Mode**: Players know their role from the start
- **Mystery Mode**: Players don't initially know if they are the imposter — adding an extra layer of deduction!

## Tech Stack

- **Backend**: FastAPI with WebSocket support
- **Frontend**: HTML5, Jinja2 templates, Vanilla JavaScript
- **Styling**: Custom CSS with Neon Party/Cyberpunk aesthetic
- **Python**: 3.11+

## Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) package manager (recommended) or pip

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd kochorho
```

### 2. Install Dependencies

**Using uv (Recommended):**

```bash
uv sync
```

**Using pip:**

First, create and activate a virtual environment:

```bash
python -m venv .venv
```

Activate the virtual environment:

- **Windows (PowerShell):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```

- **Windows (Command Prompt):**
  ```cmd
  .\.venv\Scripts\activate.bat
  ```

- **Linux/macOS:**
  ```bash
  source .venv/bin/activate
  ```

Then install dependencies:

```bash
pip install -e .
```

### 3. Run the Application

**Using uv:**

```bash
uv run uvicorn kochorho.main:app --app-dir src --host 0.0.0.0 --port 8001
```

**Using Python directly:**

```bash
uvicorn kochorho.main:app --app-dir src --host 0.0.0.0 --port 8001
```

### 4. Access the Game

Open your browser and navigate to:

```
http://localhost:8001
```

For local network play (parties, game nights), other devices on the same network can connect using your machine's local IP address:

```
http://<your-local-ip>:8001
```

## How to Play

1. **Create or Join a Game**
   - Enter your nickname
   - Either create a new game room or join an existing one using the room code

2. **In the Lobby**
   - Wait for other players to join (minimum 3 players recommended)
   - The host can toggle Mystery Mode on/off
   - The host starts the game when ready

3. **During a Round**
   - View your secret word (tap to reveal)
   - Discuss with other players, giving subtle clues about your word
   - The imposter must pretend they have the same word as everyone else

4. **Voting Phase**
   - Vote for who you think is the imposter
   - The player with the most votes is eliminated

5. **Win Conditions**
   - **Civilians win**: If the imposter is eliminated
   - **Imposter wins**: If they survive until only 2 players remain

## 🔧 Development

### Running in Development Mode

```bash
uvicorn kochorho.main:app --app-dir src --reload --host 0.0.0.0 --port 8001
```

The `--reload` flag enables auto-reload on code changes.

### Project Structure

```
kochorho/
├── src/
│   └── kochorho/
│       ├── __init__.py
│       ├── main.py           # FastAPI app & WebSocket routes
│       ├── game_manager.py   # Game logic & state management
│       ├── static/           # CSS and static assets
│       └── templates/        # Jinja2 HTML templates
├── pyproject.toml            # Project configuration
├── uv.lock                   # Dependency lock file
└── README.md
```
