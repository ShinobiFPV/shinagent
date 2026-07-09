"""
IMQ2 ACC Setup Generator
Researches current community meta via web search, generates a complete ACC
(Assetto Corsa Competizione) setup JSON with the active LLM backend,
validates it against tools/acc_setup_schema.py's SAFE_DEFAULTS, and sends
it to the Windows companion app (windows/acc_setup_manager.py) to save and
apply to the game.
"""
import json
import logging
import os

import requests

from config.loader import config
from tools.acc_setup_schema import fill_and_validate

log = logging.getLogger(__name__)

_NO_COMPANION = (
    "[acc_setup] Companion app not reachable. Is windows/acc_setup_manager.py "
    "running on the Windows PC?"
)


def _companion_base() -> str:
    host = config.get("acc_setups.companion_host", "192.168.1.101")
    port = config.get("acc_setups.companion_port", 8092)
    return f"http://{host}:{port}"


def _web_search(query: str, max_results: int = 2) -> list:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        log.warning(f"acc_setup web search failed for '{query}': {e}")
        return []


def _research_meta(car: str, track: str, session_type: str, weather: str) -> str:
    queries = [
        f"{car} ACC setup {track} 2024 2025 meta",
        f"{car} ACC {track} {session_type} setup guide",
        f"ACC {track} setup tips weather {weather}",
    ]
    findings = []
    for q in queries:
        for res in _web_search(q):
            snippet = (res.get("content") or "")[:400]
            if snippet:
                findings.append(f"- {res.get('title', '')}: {snippet}")
    if not findings:
        return "No web search results found — generate from known GT3 setup principles and SAFE_DEFAULTS."
    return "\n".join(findings[:12])


def _build_prompt(car: str, track: str, session_type: str, weather: str,
                   ambient_temp: int, track_temp: int, research: str, notes: str) -> str:
    extra = f"\nSpecial requirements from the driver: {notes}" if notes else ""
    return f"""You are an ACC (Assetto Corsa Competizione) setup engineer.
Generate a complete, valid ACC setup JSON file for:

Car: {car}
Track: {track}
Session: {session_type}
Weather: {weather}
Ambient temp: {ambient_temp}C  Track temp: {track_temp}C{extra}

Research findings from the web:
{research}

Requirements:
- Output ONLY valid JSON matching the ACC setup file format (basicSetup/advancedSetup structure).
- Start from safe GT3 defaults, adjust based on research and conditions — never invent random values.
- Tyre pressures: adjust for temperature — higher ambient temp needs lower cold pressure. Hot target
  is typically 27.3-27.8 PSI. ACC's tyrePressure values are raw units (not PSI directly), typically
  in the 49-65 range for slicks. Use the research findings for the specific car/track if available;
  otherwise use safe GT3 defaults and note the assumption in your notes.
- For {weather} conditions: dry = tyreCompound 0 (slick); wet = tyreCompound 1, increase brakeDuct,
  reduce wing levels.
- For {session_type}: sprint = qualify-style setup, max performance, fresh tyres; endurance =
  conservative tyre wear and a fuel strategy with pit stops; qualifying = maximum grip, minimum
  fuel, aggressive aero.
- Include a pitStrategy array if nPitStops > 0, one entry per stop.
- All 4-element arrays (tyrePressure, camber, toe, wheelRate, bumpStopRateUp/Dn, bumpStopWindow,
  rodLength, singleJounce, wheelBase, bumpSlow/Fast, reboundSlow/Fast) must have exactly 4 elements,
  in [FL, FR, RL, RR] order.
- rideHeight and brakeDuct must have exactly 2 elements: [front, rear].

Output strict JSON in exactly this shape and nothing else — no markdown fences, no commentary:
{{"setup": {{...complete ACC setup JSON...}}, "notes": "brief explanation of key setup decisions"}}
"""


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    # Models sometimes wrap JSON in a ```json fence despite instructions not
    # to — strip it rather than failing the whole generation over formatting.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_acc_setup(car: str, track: str, session_type: str = "sprint",
                        weather: str = "dry", ambient_temp: int = 22,
                        track_temp: int = 28, notes: str = "") -> str:
    """
    Research current meta, generate a complete ACC setup JSON with the
    active LLM backend, validate it, and send it to the companion app to
    save and apply. Returns a spoken-word confirmation.
    """
    try:
        from core.llm import get_llm_backend

        research = _research_meta(car, track, session_type, weather)
        prompt = _build_prompt(car, track, session_type, weather, ambient_temp, track_temp, research, notes)

        llm = get_llm_backend()
        response = llm.complete(
            messages=[{"role": "user", "content": "Generate the setup now."}],
            system=prompt,
            max_tokens=2000,
        )

        try:
            parsed = _parse_llm_json(response.text)
        except Exception as e:
            log.error(f"acc_setup: failed to parse LLM response as JSON: {e}\nRaw: {response.text[:500]}")
            return "[generate_acc_setup] The setup generator returned something that wasn't valid JSON — try again."

        raw_setup = parsed.get("setup", {}) or {}
        setup_notes = (parsed.get("notes") or "").strip()
        raw_setup["carName"] = raw_setup.get("carName") or car
        setup = fill_and_validate(raw_setup)

        name = f"{car} {track} {session_type} {weather} Q2".replace("_", " ").title()

        base = _companion_base()
        save_resp = requests.post(
            f"{base}/setups",
            json={
                "name": name, "car": car, "track": track,
                "session_type": session_type, "weather": weather,
                "ambient_temp": ambient_temp, "track_temp": track_temp,
                "notes": setup_notes, "json_data": setup,
            },
            timeout=10,
        )
        save_resp.raise_for_status()
        save_data = save_resp.json()
        if not save_data.get("ok"):
            return f"[generate_acc_setup] Companion app rejected the setup: {save_data.get('error', 'unknown error')}"

        setup_id = save_data["id"]

        apply_resp = requests.post(f"{base}/setups/{setup_id}/apply", timeout=10)
        apply_ok = apply_resp.ok and apply_resp.json().get("ok")

        summary = f"Setup generated for {car} at {track}. {setup_notes}".strip()
        if apply_ok:
            summary += f" Applied to ACC as '{name}'. Open the garage screen and load it."
        else:
            summary += f" Saved as '{name}' but could not auto-apply — use apply_acc_setup({setup_id})."
        return summary

    except requests.exceptions.RequestException:
        return _NO_COMPANION
    except Exception as e:
        log.error(f"generate_acc_setup error: {e}", exc_info=True)
        return f"[generate_acc_setup] Error: {e}"


def _fmt_relative_time(iso_str) -> str:
    if not iso_str:
        return "not yet applied"
    try:
        from datetime import datetime, timezone
        # sqlite's CURRENT_TIMESTAMP (used for both created_at and
        # applied_at) is UTC, not local time — comparing against
        # datetime.now() here would misreport the delta by the local
        # timezone offset (e.g. a setup applied seconds ago showing as
        # "20h ago"). Compare in UTC throughout instead.
        dt = datetime.fromisoformat(iso_str).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        if delta.days <= 0:
            hours = delta.seconds // 3600
            return "just now" if hours <= 0 else f"applied {hours}h ago"
        if delta.days == 1:
            return "applied yesterday"
        return f"applied {delta.days} days ago"
    except Exception:
        return f"applied {iso_str}"


def list_acc_setups(car: str = None, track: str = None) -> str:
    try:
        base = _companion_base()
        params = {k: v for k, v in (("car", car), ("track", track)) if v}
        r = requests.get(f"{base}/setups", params=params, timeout=10)
        r.raise_for_status()
        setups = r.json().get("setups", [])
        if not setups:
            scope = " ".join(b for b in (car, track) if b)
            return f"No saved setups{(' for ' + scope) if scope else ''}."

        header_bits = [b for b in (car, track) if b]
        header = f"Saved setups for {' at '.join(header_bits)}:" if header_bits else "Saved setups:"
        lines = [header]
        for i, s in enumerate(setups, 1):
            lines.append(f"{i}. {s['name']} (id {s['id']}, {_fmt_relative_time(s.get('applied_at'))})")
        return "\n".join(lines)
    except requests.exceptions.RequestException:
        return _NO_COMPANION
    except Exception as e:
        return f"[list_acc_setups] Error: {e}"


def apply_acc_setup(setup_id: int) -> str:
    try:
        base = _companion_base()
        get_r = requests.get(f"{base}/setups/{setup_id}", timeout=10)
        if get_r.status_code == 404:
            return f"[apply_acc_setup] No setup with ID {setup_id}."
        get_r.raise_for_status()
        name = get_r.json().get("setup", {}).get("name", f"#{setup_id}")

        r = requests.post(f"{base}/setups/{setup_id}/apply", timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return f"[apply_acc_setup] Failed: {data.get('error', 'unknown error')}"
        return f"Setup '{name}' applied to ACC. Load it from your garage screen."
    except requests.exceptions.RequestException:
        return _NO_COMPANION
    except Exception as e:
        return f"[apply_acc_setup] Error: {e}"


def delete_acc_setup(setup_id: int) -> str:
    try:
        base = _companion_base()
        get_r = requests.get(f"{base}/setups/{setup_id}", timeout=10)
        if get_r.status_code == 404:
            return f"[delete_acc_setup] No setup with ID {setup_id}."
        get_r.raise_for_status()
        name = get_r.json().get("setup", {}).get("name", f"#{setup_id}")

        r = requests.delete(f"{base}/setups/{setup_id}", json={"delete_file": False}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return f"[delete_acc_setup] Failed: {data.get('error', 'unknown error')}"
        return f"Setup '{name}' deleted."
    except requests.exceptions.RequestException:
        return _NO_COMPANION
    except Exception as e:
        return f"[delete_acc_setup] Error: {e}"
