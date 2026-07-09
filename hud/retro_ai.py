"""
Q2 Retro AI Controller
=======================
Q2 plays as Player 2 using RetroArch RAM reads for game state
and vgamepad for input injection.

Architecture:
    - Frame window: every 250-500ms Q2 can make one action decision
    - Game state: read from RetroArch RAM via READ_CORE_RAM
    - Decision: LLM or rule-based depending on ai_mode setting
    - Input: sent to vgamepad virtual controller

AI modes:
    RULES:    Simple rule-based AI, no LLM calls, very fast
    LLM:      Q2's LLM makes decisions (slower, more adaptive)
    HYBRID:   Rules for moment-to-moment, LLM for strategy
    IDLE:     Q2 is connected but not pressing buttons

LLM-mode decisions go through the HUD's own /api/retro/decide route (which
proxies to Q2's webapp /retro/decide -> tools/retro_decide.py), a one-shot,
stateless call to the raw LLM backend -- NOT Q2's conversational
IMQ2Agent.chat(). Routing "press RIGHT+B" decisions through the full agent
would store every frame as an episodic memory turn and run headfirst into
the Vernacular Generator actively rewriting the response into conversational
prose instead of a clean JSON array. See tools/retro_decide.py's docstring.
"""

import threading
import time
import random
import re
import requests
import json
from typing import Optional, Callable

from hud.retro_manager import CORES


# ── Known RAM addresses for popular games ────────────────────────
# These are the addresses Q2 reads to understand game state.
# Expand this as more games are supported. Best-effort / community-sourced
# -- verify against RetroArch's own Memory Viewer (Tools > Memory Viewer)
# before relying on them for anything beyond an approximate read.

GAME_RAM_MAPS = {
    # Street Fighter II (SNES)
    'street fighter ii': {
        'p1_health':   0x7E0D5A,
        'p2_health':   0x7E0D5B,
        'p1_x':        0x7E0D00,
        'p2_x':        0x7E0D40,
        'timer':       0x7E0BD2,
        'round':       0x7E0B3B,
    },
    # Mortal Kombat (SNES)
    'mortal kombat': {
        'p1_health':   0x7E0E06,
        'p2_health':   0x7E0E07,
    },
    # Super Mario Bros (NES)
    'super mario bros': {
        'mario_x':     0x006D,
        'mario_y':     0x00CE,
        'lives':       0x075A,
        'world':       0x075F,
        'coins':       0x07ED,
        'timer_h':     0x07F8,
    },
    # Sonic the Hedgehog (Genesis)
    'sonic the hedgehog': {
        'rings':       0xFFFF20,
        'lives':       0xFFFF76,
        'score':       0xFFFF26,
        'sonic_x':     0xFFB010,
        'sonic_y':     0xFFB014,
    },
    # Contra (NES)
    'contra': {
        'p1_lives':    0x0072,
        'p2_lives':    0x0073,
        'p1_x':        0x004C,
        'p1_y':        0x0050,
    },
}


class RetroAIController:
    """
    Q2's AI player. Reads game RAM and controls Player 2.
    """

    def __init__(self, manager, q2_base: str = 'http://127.0.0.1:8094',
                 ai_mode: str = 'hybrid', aggression: float = 0.5):
        self._manager   = manager
        self._q2_base   = q2_base  # the HUD's own Flask server (proxies to Q2)
        self._ai_mode   = ai_mode  # rules, llm, hybrid, idle
        self._aggression = aggression  # 0.0 to 1.0
        self._running   = False
        self._thread    = None
        self._frame_ms  = 400   # decision interval
        self._game_name = None
        self._ram_map   = None
        self._commentary_cb: Optional[Callable] = None
        self._last_state = {}
        self._action_history = []

    def start(self, game_name: str):
        """Start AI control for a game."""
        self._game_name = game_name.lower()

        # Find RAM map for this game
        self._ram_map = None
        for key, map_data in GAME_RAM_MAPS.items():
            if key in self._game_name:
                self._ram_map = map_data
                break

        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._manager.p2.release_all()

    def set_aggression(self, level: float):
        """Set aggression 0.0 (passive) to 1.0 (aggressive)."""
        self._aggression = max(0.0, min(1.0, level))

    def set_mode(self, mode: str):
        """Set AI mode: rules, llm, hybrid, idle."""
        self._ai_mode = mode

    def on_commentary(self, callback: Callable):
        """Register callback for AI commentary text."""
        self._commentary_cb = callback

    def _emit_commentary(self, text: str):
        if text and self._commentary_cb:
            self._commentary_cb(text)

    def _read_game_state(self) -> dict:
        """Read relevant RAM addresses for current game."""
        state = {}
        if not self._ram_map:
            return state

        ra = self._manager.ra
        for key, address in self._ram_map.items():
            values = ra.read_ram(address, 2)
            if values:
                state[key] = values[0] if len(values) == 1 else values

        return state

    def _decide_rules(self, state: dict) -> Optional[list]:
        """
        Rule-based AI decisions. Fast, no API calls.
        Returns list of button names to press, or None.
        """
        game = self._game_name or ""

        # Generic fighting game rules
        if 'street fighter' in game or 'mortal kombat' in game:
            p2_health = state.get('p2_health', 100)

            if p2_health < 20:
                # Low health -- defensive and try to escape
                return ['LEFT']

            # Alternate between attacking and blocking
            roll = random.random()
            if roll < self._aggression:
                # Attack
                if roll < self._aggression * 0.3:
                    return ['B']  # punch
                elif roll < self._aggression * 0.6:
                    return ['A']  # kick
                else:
                    return ['DOWN', 'B']  # crouching attack
            else:
                return None  # no input = blocking in some games

        # Generic platformer rules
        if 'mario' in game or 'sonic' in game or 'contra' in game:
            roll = random.random()
            if roll < 0.3:
                return ['RIGHT']
            elif roll < 0.5:
                return ['B']  # jump
            elif roll < 0.6:
                return ['RIGHT', 'B']  # run-jump
            else:
                return None

        return None

    def _decide_llm(self, state: dict) -> Optional[list]:
        """
        Ask Q2's LLM (via the HUD's own /api/retro/decide proxy, a
        stateless one-shot call -- see module docstring) to decide the
        next action. Returns list of button names or None.
        """
        try:
            system_name = self._manager.current_system or 'unknown'
            buttons = CORES.get(system_name, {}).get('buttons', [])

            payload = {
                'game': self._game_name or 'unknown game',
                'system': system_name,
                'state': state,
                'aggression': self._aggression,
                'buttons': buttons,
                'recent_actions': self._action_history[-5:],
            }

            r = requests.post(
                f'{self._q2_base}/api/retro/decide',
                json=payload, timeout=3.0)

            if r.status_code == 200:
                data = r.json()
                if data.get('ok'):
                    result = data.get('buttons')
                    return result if isinstance(result, list) else None

        except Exception:
            pass

        return None

    def _execute_action(self, buttons: list):
        """Execute a list of button presses."""
        p2 = self._manager.p2

        if not buttons:
            p2.release_all()
            return

        if len(buttons) == 1:
            p2.press(buttons[0], duration_ms=int(self._frame_ms * 0.6))
        else:
            p2.combo(buttons, duration_ms=int(self._frame_ms * 0.5))

        self._action_history.append(buttons)
        if len(self._action_history) > 20:
            self._action_history.pop(0)

    def _loop(self):
        """Main AI loop."""
        last_commentary = 0

        while self._running:
            loop_start = time.time()

            try:
                # Read game state
                state = self._read_game_state()
                self._last_state = state

                if self._ai_mode == 'idle':
                    pass  # connected but not playing

                elif self._ai_mode == 'rules':
                    buttons = self._decide_rules(state)
                    if buttons:
                        self._execute_action(buttons)

                elif self._ai_mode == 'llm':
                    buttons = self._decide_llm(state)
                    if buttons is not None:
                        self._execute_action(buttons)

                elif self._ai_mode == 'hybrid':
                    # Use rules for rapid decisions, LLM for strategy
                    if random.random() < 0.15:  # 15% LLM calls
                        buttons = self._decide_llm(state)
                    else:
                        buttons = self._decide_rules(state)
                    if buttons is not None:
                        self._execute_action(buttons)

                # Periodic commentary (every ~30 seconds)
                if time.time() - last_commentary > 30:
                    self._emit_commentary(
                        self._generate_commentary(state))
                    last_commentary = time.time()

            except Exception:
                pass

            # Wait for next frame window
            elapsed = time.time() - loop_start
            sleep_time = max(0, self._frame_ms/1000 - elapsed)
            time.sleep(sleep_time)

    def _generate_commentary(self, state: dict) -> str:
        """Generate a commentary line about the current game state."""
        if not state:
            return ""

        # Simple commentary based on state
        if 'p2_health' in state:
            hp = state['p2_health']
            if hp < 20:
                return f"P2 health critical at {hp}!"
            elif hp > 80:
                return f"P2 looking strong with {hp} health."

        if 'lives' in state:
            return f"Lives remaining: {state['lives']}"

        return ""
