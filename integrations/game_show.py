"""
Game Show Mode -- Who Wants a Hundred Bucks?
============================================
Holds game state, question sets, lifelines, and scoring. All 15 questions
are generated once via the LLM at game start (tools/game_show.py's
generate_game()/start_game()) -- no LLM calls happen during play itself,
so answer_question()/use_lifeline()/walk_away() are plain deterministic
Python, safe to call either from Q2's normal tool loop (voice) or directly
from face/server.py's HTTP routes (kiosk controller fast path).
"""

import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

GAME_CACHE = Path(__file__).parent.parent / "cache" / "game_show"

# ── Money ladder ─────────────────────────────────────────

MONEY_LADDER = [
    {"level": 1,  "value": "$1",    "safe": False},
    {"level": 2,  "value": "$2",    "safe": False},
    {"level": 3,  "value": "$3",    "safe": False},
    {"level": 4,  "value": "$5",    "safe": False},
    {"level": 5,  "value": "$10",   "safe": True},   # first safe haven
    {"level": 6,  "value": "$15",   "safe": False},
    {"level": 7,  "value": "$20",   "safe": False},
    {"level": 8,  "value": "$25",   "safe": False},
    {"level": 9,  "value": "$50",   "safe": False},
    {"level": 10, "value": "$75",   "safe": True},   # second safe haven
    {"level": 11, "value": "$80",   "safe": False},
    {"level": 12, "value": "$85",   "safe": False},
    {"level": 13, "value": "$90",   "safe": False},
    {"level": 14, "value": "$95",   "safe": False},
    {"level": 15, "value": "$100",  "safe": False},  # the big one
]

# ── Question difficulty distribution ─────────────────────
# difficulty setting (1-5) -> per-question (question_num, difficulty 1-5)

DIFFICULTY_DISTRIBUTION = {
    1: [(1,1),(2,1),(3,1),(4,1),(5,1),
        (6,2),(7,2),(8,2),(9,2),(10,2),
        (11,3),(12,3),(13,3),(14,3),(15,4)],
    2: [(1,1),(2,1),(3,1),(4,2),(5,2),
        (6,2),(7,3),(8,3),(9,3),(10,3),
        (11,4),(12,4),(13,4),(14,4),(15,5)],
    3: [(1,1),(2,2),(3,2),(4,3),(5,3),
        (6,3),(7,3),(8,4),(9,4),(10,4),
        (11,4),(12,5),(13,5),(14,5),(15,5)],
    4: [(1,2),(2,2),(3,3),(4,3),(5,3),
        (6,4),(7,4),(8,4),(9,4),(10,5),
        (11,5),(12,5),(13,5),(14,5),(15,5)],
    5: [(1,3),(2,3),(3,3),(4,4),(5,4),
        (6,4),(7,4),(8,5),(9,5),(10,5),
        (11,5),(12,5),(13,5),(14,5),(15,5)],
}

# ── Question categories (exactly 15 -- one per question, sampled without
# replacement in tools/game_show.py's generate_game()) ────

CATEGORIES = [
    "General Knowledge", "Science", "History", "Geography",
    "Pop Culture", "Sports", "Entertainment", "Literature",
    "Technology", "Food & Drink", "Nature", "Math",
    "Movies", "Music", "Art",
]

# ── Difficulty descriptions for the generation prompt ────

DIFFICULTY_DESC = {
    1: "extremely easy -- any adult should know this immediately",
    2: "easy -- common knowledge, most people know this",
    3: "medium -- requires some general knowledge",
    4: "hard -- specialist knowledge, many people unsure",
    5: "very hard -- expert level, most people will not know",
}


@dataclass
class Question:
    """A single trivia question."""
    number:   int
    level:    int          # 1-15 (money ladder position)
    value:    str          # "$10", "$100" etc.
    text:     str
    answers:  list         # ["A. ...", "B. ...", "C. ...", "D. ..."]
    correct:  str          # "A", "B", "C", or "D"
    category: str
    fun_fact: str = ""     # shown after correct answer


@dataclass
class Lifeline:
    """State of a single lifeline."""
    name:      str
    used:      bool = False


@dataclass
class GameState:
    """Complete game state for one play-through."""
    game_id:      str
    difficulty:   int              # 1-5
    questions:    list             # list[Question]
    current_q:    int = 0          # 0-indexed
    score_level:  int = 0          # 0 = haven't won anything yet
    safe_level:   int = 0          # last safe haven reached
    active:       bool = True
    ended:        bool = False
    walked_away:  bool = False
    started_at:   float = field(default_factory=time.time)

    lifelines: dict = field(default_factory=lambda: {
        "fifty_fifty":  Lifeline("50/50"),
        "phone_friend": Lifeline("Phone a Friend"),
        "ask_audience": Lifeline("Ask the Audience"),
    })

    @property
    def current_question(self) -> Optional[Question]:
        if self.current_q < len(self.questions):
            return self.questions[self.current_q]
        return None

    @property
    def current_value(self) -> str:
        q = self.current_question
        return q.value if q else "$0"

    @property
    def safe_value(self) -> str:
        best = "$0"
        for rung in MONEY_LADDER:
            if rung["safe"] and rung["level"] <= self.score_level:
                best = rung["value"]
        return best

    def to_dict(self) -> dict:
        q  = self.current_question
        ll = self.lifelines

        return {
            "game_id":       self.game_id,
            "active":        self.active,
            "ended":         self.ended,
            "walked_away":   self.walked_away,
            "difficulty":    self.difficulty,
            "current_q_num": self.current_q + 1,
            "total_q":       len(self.questions),
            "score_level":   self.score_level,
            "safe_level":    self.safe_level,
            "current_value": self.current_value,
            "safe_value":    self.safe_value,
            "money_ladder":  MONEY_LADDER,
            "current_question": {
                "text":     q.text,
                "answers":  q.answers,
                "category": q.category,
                "value":    q.value,
                "level":    q.level,
            } if q else None,
            "lifelines": {
                k: {"name": v.name, "used": v.used}
                for k, v in ll.items()
            },
        }


# ── Singleton (one game at a time -- single-user system) ──

_game: Optional[GameState] = None

def get_game() -> Optional[GameState]:
    return _game

def set_game(g: GameState):
    global _game
    _game = g

def clear_game():
    global _game
    _game = None
