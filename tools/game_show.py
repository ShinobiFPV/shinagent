"""
Game Show Tools -- Who Wants a Hundred Bucks?
===============================================
Plain functions (wrapped as BaseTool subclasses in tools/registry.py) that
back the game. Questions are generated once via generate_game()/start_game()
at game start; answer_question()/use_lifeline()/walk_away() are pure Python
after that -- no LLM calls during play, and safe to call directly from
face/server.py's HTTP routes for the kiosk's controller fast path, not just
through Q2's normal tool loop.
"""

import json
import random
import uuid

from integrations.game_show import (
    GameState, Question, MONEY_LADDER, DIFFICULTY_DISTRIBUTION,
    DIFFICULTY_DESC, CATEGORIES, get_game, set_game,
)


def generate_game(difficulty: int = 3) -> str:
    """
    Returns the prompt Q2 should use to generate 15 trivia questions.
    Call this first; feed the LLM-generated JSON array to start_game().
    """
    difficulty = max(1, min(5, int(difficulty)))
    dist = DIFFICULTY_DISTRIBUTION[difficulty]
    cats = random.sample(CATEGORIES, 15)

    questions_spec = [
        f"Q{qnum} ({MONEY_LADDER[i]['value']}, {DIFFICULTY_DESC[qdiff]}, "
        f"category: {cats[i]})"
        for i, (qnum, qdiff) in enumerate(dist)
    ]

    return f"""Generate 15 trivia questions for "Who Wants a Hundred Bucks?"

Difficulty setting: {difficulty}/5
Questions must get progressively harder.

For each question provide:
- text: the question
- answers: exactly 4 options as ["A. ...", "B. ...", "C. ...", "D. ..."]
- correct: one of "A", "B", "C", "D"
- category: the category
- fun_fact: an interesting fact about the answer (1 sentence)

Question specifications:
{chr(10).join(questions_spec)}

Rules:
- All wrong answers must be plausible (not obviously wrong)
- Questions must be factually accurate
- Vary the position of the correct answer (not always A or B)
- Fun facts must be genuinely interesting
- No repeat topics across the 15 questions

Return ONLY a valid JSON array of 15 objects, in question order. No other text.
Example format:
[
  {{
    "text": "What is the capital of France?",
    "answers": ["A. Berlin", "B. Madrid", "C. Paris", "D. Rome"],
    "correct": "C",
    "category": "Geography",
    "fun_fact": "Paris has been the capital since 987 AD."
  }}
]"""


def start_game(difficulty: int = 3, questions_json: str = "") -> str:
    """
    Start a new game with pre-generated questions.
    difficulty: 1-5 (1=easiest, 5=hardest)
    questions_json: JSON array of 15 questions from generate_game()'s prompt
    """
    difficulty = max(1, min(5, int(difficulty)))

    if not questions_json:
        return ("ERROR: No questions provided. Call generate_game() first "
                "to get the questions prompt, generate the 15 questions, "
                "then call start_game() with the questions JSON.")

    try:
        raw = questions_json.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1])
        q_data = json.loads(raw)
    except Exception as e:
        return f"ERROR parsing questions JSON: {e}"

    if len(q_data) < 15:
        return f"ERROR: Need 15 questions, got {len(q_data)}. Regenerate the questions."

    questions = []
    for i, qd in enumerate(q_data[:15]):
        rung = MONEY_LADDER[i]
        questions.append(Question(
            number=i + 1,
            level=rung["level"],
            value=rung["value"],
            text=qd.get("text", ""),
            answers=qd.get("answers", []),
            correct=str(qd.get("correct", "A")).upper(),
            category=qd.get("category", "General"),
            fun_fact=qd.get("fun_fact", ""),
        ))

    game = GameState(
        game_id=str(uuid.uuid4())[:8],
        difficulty=difficulty,
        questions=questions,
    )
    set_game(game)
    _emit_game_event({"type": "game_start", "state": game.to_dict()})

    q1 = questions[0]
    return (
        f"Game started! 15 questions ready. Difficulty: {difficulty}/5. "
        f"Starting with {q1.value}: {q1.text}\n"
        f"Answers: {', '.join(q1.answers)}"
    )


def answer_question(answer: str) -> str:
    """
    Submit an answer for the current question.
    answer: "A", "B", "C", or "D"
    """
    game = get_game()
    if not game or not game.active:
        return "No active game."

    answer = answer.upper().strip()
    if answer not in ("A", "B", "C", "D"):
        return f"Invalid answer '{answer}'. Must be A, B, C, or D."

    q = game.current_question
    if not q:
        return "No current question."

    is_correct = answer == q.correct

    if is_correct:
        game.score_level = q.level
        if MONEY_LADDER[q.level - 1]["safe"]:
            game.safe_level = q.level

        if game.current_q < 14:
            game.current_q += 1
            next_q = game.current_question

            _emit_game_event({
                "type": "answer_correct", "answer": answer,
                "fun_fact": q.fun_fact, "won": q.value,
                "state": game.to_dict(),
            })
            return (
                f"CORRECT! {answer} was right. Fun fact: {q.fun_fact}\n"
                f"Won {q.value}! Next question ({next_q.value}): {next_q.text}\n"
                f"Answers: {', '.join(next_q.answers)}"
            )
        else:
            game.active = False
            game.ended = True
            _emit_game_event({
                "type": "game_won", "answer": answer, "state": game.to_dict(),
            })
            return (
                f"CORRECT! YOU WIN $100! The answer was {answer}. "
                f"Fun fact: {q.fun_fact}"
            )
    else:
        game.active = False
        game.ended = True
        safe_val = game.safe_value

        _emit_game_event({
            "type": "answer_wrong", "answer": answer, "correct": q.correct,
            "safe_val": safe_val, "state": game.to_dict(),
        })
        return (
            f"WRONG! The correct answer was {q.correct}. "
            f"You leave with {safe_val}. Fun fact: {q.fun_fact}"
        )


def use_lifeline(lifeline: str) -> str:
    """lifeline: "fifty_fifty", "phone_friend", or "ask_audience" """
    game = get_game()
    if not game or not game.active:
        return "No active game."

    ll = game.lifelines.get(lifeline)
    if not ll:
        return f"Unknown lifeline: {lifeline}"
    if ll.used:
        return f"The {ll.name} lifeline has already been used."

    q = game.current_question
    if not q:
        return "No current question."

    ll.used = True

    if lifeline == "fifty_fifty":
        wrong = [l for l in "ABCD" if l != q.correct]
        remove = random.sample(wrong, 2)
        remaining = [a for a in q.answers if a[0] not in remove]
        result = f"50/50 removes {remove[0]} and {remove[1]}. Remaining: {', '.join(remaining)}"

        _emit_game_event({
            "type": "lifeline_fifty_fifty", "remove": remove,
            "remaining": [a[0] for a in remaining], "state": game.to_dict(),
        })
        return result

    elif lifeline == "phone_friend":
        # Correct-answer confidence scales down with difficulty; sometimes
        # wrong on purpose for drama, same as the real show's friends.
        diff = game.difficulty
        confidence = random.randint(max(40, 90 - diff * 12), min(95, 90 - diff * 5))
        friend_correct = random.random() < (confidence / 100)
        friend_answer = q.correct if friend_correct else random.choice(
            [l for l in "ABCD" if l != q.correct])

        friend_lines = [
            f"I'm about {confidence}% sure it's {friend_answer}.",
            f"I think it's {friend_answer}, but don't hold me to it.",
            f"{friend_answer}? Yeah, I'm pretty sure it's {friend_answer}.",
            f"Hmm... I'd go with {friend_answer}. {confidence}% confident.",
        ]
        result = random.choice(friend_lines)

        _emit_game_event({
            "type": "lifeline_phone", "friend_answer": friend_answer,
            "confidence": confidence, "state": game.to_dict(),
        })
        return f"Your friend says: '{result}'"

    elif lifeline == "ask_audience":
        diff = game.difficulty
        correct_pct = random.randint(max(30, 75 - diff * 8), min(90, 75 - diff * 3))
        remaining = 100 - correct_pct
        others = [l for l in "ABCD" if l != q.correct]
        o1 = random.randint(0, remaining - 2)
        o2 = random.randint(0, remaining - o1 - 1)
        o3 = remaining - o1 - o2

        poll = {q.correct: correct_pct}
        for pct, letter in zip([o1, o2, o3], others):
            poll[letter] = pct

        poll_str = "  ".join(f"{l}: {poll.get(l, 0)}%" for l in "ABCD")

        _emit_game_event({"type": "lifeline_audience", "poll": poll, "state": game.to_dict()})
        return f"The audience votes:\n{poll_str}"

    return "Unknown lifeline."


def walk_away() -> str:
    """Player walks away with the current safe-haven amount."""
    game = get_game()
    if not game or not game.active:
        return "No active game."

    game.active = False
    game.ended = True
    game.walked_away = True
    safe_val = game.safe_value
    current_val = game.current_value
    correct = game.current_question.correct if game.current_question else "?"

    _emit_game_event({
        "type": "walk_away", "won": safe_val, "gave_up": current_val,
        "state": game.to_dict(),
    })
    return (
        f"You walked away with {safe_val}. You were playing for {current_val}. "
        f"The correct answer was {correct}."
    )


def get_game_state() -> str:
    """Get a text summary of the current game state."""
    game = get_game()
    if not game:
        return "No active game."

    q = game.current_question
    if not q:
        return "Game over."

    lifeline_status = ", ".join(
        f"{v.name} {'(used)' if v.used else '(available)'}"
        for v in game.lifelines.values()
    )
    return (
        f"Question {game.current_q + 1}/15 worth {q.value}: {q.text}\n"
        f"Answers: {', '.join(q.answers)}\n"
        f"Lifelines: {lifeline_status}"
    )


def _emit_game_event(event: dict):
    """In-process, not an HTTP round-trip -- tools run in the same process
    as face/server.py's background thread (main.py starts both), same
    pattern as voice/talk_button.py's ControllerBridge calling
    emit_controller_event() directly."""
    try:
        from face.server import emit_game_event
        emit_game_event(event)
    except Exception:
        pass
