"""
Bluff Card Game — Multiplayer Server
=====================================
Uses aiohttp for HTTP serving and python-socketio for real-time WebSocket
communication. No Flask dependency. The single server serves the frontend
HTML file AND handles all game logic over Socket.IO.

Run:
    python app.py
Then open http://localhost:8080 in your browser.
"""

import os
import random
from collections import Counter

# pyrefly: ignore [missing-import]
import socketio
# pyrefly: ignore [missing-import]
from aiohttp import web

# ---------------------------------------------------------------------------
# Socket.IO + aiohttp setup
# ---------------------------------------------------------------------------
sio = socketio.AsyncServer(async_mode="aiohttp", cors_allowed_origins="*")
app = web.Application()
sio.attach(app)

# ---------------------------------------------------------------------------
# Game constants & state
# ---------------------------------------------------------------------------
VALUES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]

rooms: dict = {}
player_sessions: dict = {}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def create_deck():
    deck = [{"suit": suit, "value": value} for suit in SUITS for value in VALUES]
    random.shuffle(deck)
    return deck


def build_room_payload(room):
    players = [p["username"] for p in room["players"]]
    host = players[0] if players else None
    return {
        "room": room["id"],
        "players": players,
        "host": host,
        "started": room["game_started"],
    }


def build_game_payload(room):
    players = [p["username"] for p in room["players"]]
    current_turn = players[room["turn_index"]] if players else None
    active_claim = room["last_play"]["claim_rank"] if room["last_play"] else None
    hand_counts = {name: len(room["hands"][name]) for name in players}
    return {
        "players": players,
        "current_turn": current_turn,
        "active_claim": active_claim,
        "hand_counts": hand_counts,
        "pile_size": len(room["discard_pile"]),
        "last_action": room["last_action"],
        "host": players[0] if players else None,
        "started": room["game_started"],
    }


async def emit_room_update(room):
    await sio.emit("room_update", build_room_payload(room), to=room["id"])


async def emit_private_state(room):
    base_payload = build_game_payload(room)
    for player in room["players"]:
        sid = player["sid"]
        username = player["username"]
        await sio.emit(
            "state_sync",
            {
                **base_payload,
                "hand": room["hands"][username],
                "your_name": username,
            },
            to=sid,
        )


def get_room(room_id):
    room = rooms.get(room_id)
    if not room:
        return None, None
    players = [p["username"] for p in room["players"]]
    return room, players


def reset_round(room, winner_name):
    room["last_action"] = {
        "type": "round_end",
        "message": f"{winner_name} emptied their hand and won the match.",
    }
    room["game_started"] = False


# ---------------------------------------------------------------------------
# HTTP route — serve the frontend
# ---------------------------------------------------------------------------
async def index_handler(request):
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    return web.FileResponse(html_path)


app.router.add_get("/", index_handler)


# ---------------------------------------------------------------------------
# Socket.IO event handlers
# ---------------------------------------------------------------------------
@sio.event
async def connect(sid, environ):
    print(f"[connect] {sid}")


@sio.event
async def join_game(sid, data):
    room_id = str(data.get("room", "")).strip().upper()
    username = str(data.get("username", "")).strip()

    if not room_id or not username:
        await sio.emit(
            "error_message",
            {"message": "Please enter a valid name and room code."},
            to=sid,
        )
        return

    if room_id not in rooms:
        rooms[room_id] = {
            "id": room_id,
            "players": [],
            "hands": {},
            "deck": [],
            "discard_pile": [],
            "turn_index": 0,
            "game_started": False,
            "last_play": None,
            "last_action": {"type": "info", "message": "Waiting for players."},
        }

    room = rooms[room_id]
    usernames = [p["username"] for p in room["players"]]

    if room["game_started"]:
        await sio.emit(
            "error_message",
            {"message": "This room is already in an active game."},
            to=sid,
        )
        return

    if username in usernames:
        await sio.emit(
            "error_message",
            {"message": "That name is already taken in this room."},
            to=sid,
        )
        return

    # Join the Socket.IO room
    await sio.enter_room(sid, room_id)
    room["players"].append({"username": username, "sid": sid})
    room["hands"][username] = []
    player_sessions[sid] = {"room": room_id, "username": username}
    room["last_action"] = {
        "type": "join",
        "message": f"{username} joined room {room_id}.",
    }

    await sio.emit("join_success", {"room": room_id, "username": username}, to=sid)
    await emit_room_update(room)


@sio.event
async def start_game(sid, data):
    room_id = str(data.get("room", "")).strip().upper()
    starter = player_sessions.get(sid, {}).get("username")
    room, players = get_room(room_id)

    if not room:
        await sio.emit("error_message", {"message": "Room not found."}, to=sid)
        return

    if room["game_started"]:
        await sio.emit(
            "error_message", {"message": "The game has already started."}, to=sid
        )
        return

    if len(players) < 2:
        await sio.emit(
            "error_message",
            {"message": "At least 2 players are needed to start."},
            to=sid,
        )
        return

    if starter != players[0]:
        await sio.emit(
            "error_message",
            {"message": "Only the host can start the match."},
            to=sid,
        )
        return

    room["game_started"] = True
    room["discard_pile"] = []
    room["last_play"] = None
    room["turn_index"] = 0
    room["last_action"] = {"type": "start", "message": "The match has started."}
    hand_size_str = str(data.get("hand_size", "all")).lower()
    
    deck_count = 2 if len(players) > 4 else 1
    room["deck"] = []
    for _ in range(deck_count):
        room["deck"].extend(create_deck())
    random.shuffle(room["deck"])

    max_cards = len(room["deck"]) // len(players)
    if hand_size_str == "all":
        hand_size = max_cards
    else:
        try:
            hand_size = int(hand_size_str)
            if hand_size > max_cards:
                hand_size = max_cards
        except ValueError:
            hand_size = max_cards

    room["hands"] = {player: [] for player in players}
    for i in range(hand_size * len(players)):
        player = players[i % len(players)]
        room["hands"][player].append(room["deck"].pop())

    room["deck"] = []
    await emit_room_update(room)
    await emit_private_state(room)


@sio.event
async def play_cards(sid, data):
    room_id = str(data.get("room", "")).strip().upper()
    username = str(data.get("username", "")).strip()
    played_cards = data.get("cards", [])
    claim_rank = str(data.get("claim_rank", "")).strip()

    room, players = get_room(room_id)
    if not room or not room["game_started"]:
        await sio.emit(
            "error_message",
            {"message": "That room is not in an active game."},
            to=sid,
        )
        return

    if claim_rank not in VALUES:
        await sio.emit(
            "error_message",
            {"message": "Please select a valid rank to claim."},
            to=sid,
        )
        return

    if not played_cards:
        await sio.emit(
            "error_message",
            {"message": "Select at least one card to play."},
            to=sid,
        )
        return

    active_player = players[room["turn_index"]]
    if username != active_player:
        await sio.emit("error_message", {"message": "It is not your turn."}, to=sid)
        return

    # Check if previous player already won (edge case)
    previous_play = room["last_play"]
    if previous_play and len(room["hands"].get(previous_play["player"], [])) == 0:
        winner = previous_play["player"]
        await sio.emit("game_over", {"winner": winner}, to=room["id"])
        reset_round(room, winner)
        await emit_room_update(room)
        return

    # Validate cards against server-side hand
    server_hand = room["hands"].get(username, [])
    hand_counter = Counter((c["value"], c["suit"]) for c in server_hand)
    play_counter = Counter((c["value"], c["suit"]) for c in played_cards)

    if any(play_counter[key] > hand_counter[key] for key in play_counter):
        await sio.emit(
            "error_message",
            {"message": "One or more selected cards are invalid."},
            to=sid,
        )
        await emit_private_state(room)
        return

    # Remove played cards from hand
    for card in played_cards:
        for index, hand_card in enumerate(server_hand):
            if hand_card["value"] == card["value"] and hand_card["suit"] == card["suit"]:
                server_hand.pop(index)
                break

    room["discard_pile"].extend(played_cards)
    room["last_play"] = {
        "player": username,
        "actual_cards": played_cards,
        "claim_rank": claim_rank,
    }
    room["last_action"] = {
        "type": "play",
        "message": f"{username} played {len(played_cards)} card(s) claiming {claim_rank}s.",
    }

    # Check for immediate win (player just emptied their hand)
    if len(server_hand) == 0:
        # Don't declare winner yet — the next player has the option to call bluff
        pass

    room["turn_index"] = (room["turn_index"] + 1) % len(players)

    await emit_private_state(room)


@sio.event
async def call_bluff(sid, data):
    room_id = str(data.get("room", "")).strip().upper()
    challenger = str(data.get("username", "")).strip()

    room, players = get_room(room_id)
    if not room or not room["game_started"] or not room["last_play"]:
        await sio.emit(
            "error_message",
            {"message": "There is no recent play to challenge."},
            to=sid,
        )
        return

    last_player = room["last_play"]["player"]
    if challenger == last_player:
        await sio.emit(
            "error_message",
            {"message": "You cannot challenge your own play."},
            to=sid,
        )
        return

    actual_cards = room["last_play"]["actual_cards"]
    claim_rank = room["last_play"]["claim_rank"]
    was_bluffing = any(card["value"] != claim_rank for card in actual_cards)

    loser = last_player if was_bluffing else challenger
    room["hands"][loser].extend(room["discard_pile"])
    penalty_count = len(room["discard_pile"])
    room["discard_pile"] = []
    room["last_play"] = None
    room["turn_index"] = players.index(loser)

    if was_bluffing:
        room["last_action"] = {
            "type": "bluff_caught",
            "message": f"{challenger} caught {last_player}. {last_player} picked up {penalty_count} cards.",
        }
    else:
        room["last_action"] = {
            "type": "bad_call",
            "message": f"{challenger} challenged incorrectly and picked up {penalty_count} cards.",
        }

    await emit_private_state(room)

    # Check for winner after bluff resolution
    empty_hands = [name for name in players if len(room["hands"][name]) == 0]
    if empty_hands:
        winner = empty_hands[0]
        await sio.emit("game_over", {"winner": winner}, to=room["id"])
        reset_round(room, winner)
        await emit_room_update(room)


@sio.event
async def disconnect(sid):
    print(f"[disconnect] {sid}")
    player_data = player_sessions.pop(sid, None)
    if not player_data:
        return

    room_id = player_data["room"]
    username = player_data["username"]
    room = rooms.get(room_id)
    if not room:
        return

    room["players"] = [p for p in room["players"] if p["sid"] != sid]
    room["hands"].pop(username, None)

    if not room["players"]:
        rooms.pop(room_id, None)
        return

    room["game_started"] = False
    room["deck"] = []
    room["discard_pile"] = []
    room["last_play"] = None
    room["turn_index"] = 0
    room["last_action"] = {
        "type": "leave",
        "message": f"{username} left the room. Match reset.",
    }

    await emit_room_update(room)
    await emit_private_state(room)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("🃏 Bluff server starting on http://localhost:8080")
    web.run_app(app, host="localhost", port=8080)

