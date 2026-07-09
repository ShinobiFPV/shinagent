"""
IMQ2 MasterChef Tools
Gordon Ramsay-voiced cooking companion tools, wrapping integrations/
masterchef.py's CUISINES/RECIPES data and MasterChefSession state.
"""
import logging
import random
import subprocess
import time

log = logging.getLogger(__name__)

_FREE_CHOICE_ALIASES = {"free", "let q2 decide", "you decide", "free choice", "gordon decide", ""}

# Ingredient-keyword -> Gordon's quality note, checked against every
# shopping-list line so brand/quality guidance shows up automatically
# rather than needing to be hand-attached per recipe.
_QUALITY_NOTES = [
    ("de cecco", "NOT supermarket own brand, I mean it."),
    ("pecorino romano", "Pecorino Romano specifically -- not Pecorino Sardo, not Grana Padano."),
    ("flank steak", "Flank or skirt steak specifically. Not sirloin."),
    ("skirt steak", "Flank or skirt steak specifically. Not sirloin."),
    ("puff pastry", "Careme or similarly good all-butter puff -- not supermarket own brand."),
    ("san marzano", "Real San Marzano, check the DOP label -- not just anything in a tin labelled 'Italian tomatoes'."),
    ("fish sauce", "A decent bottle -- Red Boat or Squid brand. The cheap stuff is mostly salt."),
    ("jasmine rice", "Actual jasmine rice, not a generic long grain."),
]


def _cuisine_key(cuisine: str) -> str:
    from integrations.masterchef import CUISINES
    c = (cuisine or "").strip().lower().replace(" ", "_").replace("-", "_")
    if c in CUISINES:
        return c
    for key, data in CUISINES.items():
        if c and c in data["name"].lower():
            return key
    return ""


def _weekday_favours_simple() -> bool:
    return time.localtime().tm_wday < 5  # Mon-Fri


def plan_meal(cuisine: str = "free", occasion: str = "weeknight", dietary_notes: str = "") -> str:
    """Propose a themed menu. cuisine can be a specific cuisine key/name,
    or 'free'/'let Q2 decide' to have Gordon pick (weighted toward
    simpler recipes on a weekday)."""
    try:
        from config.loader import config
        from integrations.masterchef import CUISINES, RECIPES

        if not config.get("masterchef.enabled", True):
            return "[plan_meal] MasterChef mode is disabled in config.yaml (masterchef.enabled)."

        key = _cuisine_key(cuisine) if cuisine.strip().lower() not in _FREE_CHOICE_ALIASES else ""
        auto_picked = False

        if not key:
            auto_picked = True
            # A configured default_cuisine (other than the "let Q2 decide"
            # placeholder) means the user has a standing preference for
            # "you pick" -- honour it instead of random-picking every time.
            configured_default = _cuisine_key(config.get("masterchef.default_cuisine", ""))
            if configured_default:
                key = configured_default
            else:
                candidates = list(CUISINES.keys())
                if _weekday_favours_simple():
                    # Prefer cuisines whose available recipes skew easy/medium on a weeknight.
                    easy_cuisines = {r["cuisine"] for r in RECIPES.values() if r["difficulty"] in ("easy", "medium")}
                    candidates = [c for c in candidates if c in easy_cuisines] or candidates
                key = random.choice(candidates)

        cuisine_data = CUISINES.get(key)
        if not cuisine_data:
            return f"[plan_meal] Don't recognise that cuisine. Try: {', '.join(CUISINES.keys())}."

        available = [(rk, r) for rk, r in RECIPES.items() if r["cuisine"] == key]
        if not available:
            return f"[plan_meal] No recipes built for {cuisine_data['name']} yet -- try another cuisine."

        # Weeknight: 1-2 dishes, nothing rated "hard". Otherwise up to 3, anything goes.
        if occasion.strip().lower() in ("weeknight", "solo", ""):
            pool = [x for x in available if x[1]["difficulty"] != "hard"] or available
            count = 1 if len(pool) == 1 else 2
        else:
            pool = available
            count = min(3, len(pool))

        random.shuffle(pool)
        menu = pool[:count]

        lines = [f"Right. Here's what we're doing{' tonight' if occasion else ''}."]
        lines.append(f"{cuisine_data['name']} -- {cuisine_data['vibe']}")
        if auto_picked:
            lines.append("(You said pick for you, so that's on me.)")
        lines.append("")
        lines.append("I'm suggesting:")
        for rk, r in menu:
            lines.append(f"- {r['name']} ({r['difficulty']}, {r['time']}, serves {r['serves']})")
        lines.append("")
        lines.append(cuisine_data["gordon_note"])
        if dietary_notes:
            lines.append(f"Noted: {dietary_notes}. I'll keep that in mind -- tell me now if any of this doesn't work.")
        lines.append("")
        lines.append("Say 'yes chef' to confirm and I'll build your shopping list. Or tell me what you want to change. But make it quick.")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"plan_meal error: {e}", exc_info=True)
        return f"[plan_meal] Error: {e}"


def build_shopping_list(dishes) -> str:
    """Build a combined, categorised, Gordon-annotated shopping list for
    one or more dishes (names or RECIPES keys)."""
    try:
        from integrations.masterchef import RECIPES, find_recipe_key

        if isinstance(dishes, str):
            dishes = [dishes]

        combined = {"proteins": [], "produce": [], "pre_made": [], "pantry": []}
        resolved_names = []
        for d in dishes or []:
            key = find_recipe_key(d)
            if not key:
                continue
            recipe = RECIPES[key]
            resolved_names.append(recipe["name"])
            for category, items in recipe.get("shopping", {}).items():
                if category in combined:
                    combined[category].extend(items)

        if not resolved_names:
            return "[build_shopping_list] Couldn't match any of those dishes -- check the names."

        for category in combined:
            combined[category] = sorted(set(combined[category]))

        lines = [f"Shopping list for: {', '.join(resolved_names)}."]

        section_labels = {
            "proteins": "PROTEINS (buy fresh today)",
            "produce": "PRODUCE (buy fresh today)",
            "pre_made": "PRE-MADE / STORE CUPBOARD (buy once, use repeatedly)",
            "pantry": "PANTRY STAPLES (you should already have these)",
        }
        for category, label in section_labels.items():
            items = combined[category]
            if not items:
                continue
            lines.append(f"\n{label}:")
            for item in items:
                note = next((n for kw, n in _QUALITY_NOTES if kw in item.lower()), None)
                lines.append(f"  - {item}" + (f"  -- {note}" if note else ""))

        return "\n".join(lines)

    except Exception as e:
        log.error(f"build_shopping_list error: {e}", exc_info=True)
        return f"[build_shopping_list] Error: {e}"


def _format_step(step: dict, gordon_intensity: int) -> str:
    lines = []
    if gordon_intensity >= 8:
        lines.append(random.choice(["Come on.", "Wake up.", "Right, listen."]))
    lines.append(f"Step {step['id']}: {step['title']}.")
    lines.append(step["gordon"])
    if step.get("technique"):
        lines.append(f"(Technique: {step['technique'].replace('_', ' ')} -- ask if you want a video.)")
    if step.get("warning") and gordon_intensity > 3:
        lines.append(f"WARNING: {step['warning']}")
    lines.append("Yes? Go. Now.")
    return "\n".join(l for l in lines if l)


def _gordon_intensity() -> int:
    from config.loader import config
    try:
        return int(config.get("masterchef.gordon_intensity", 8))
    except (TypeError, ValueError):
        return 8


def start_recipe(dish_name: str) -> str:
    """Begin cooking a specific dish -- creates/replaces the active
    session and returns Gordon's intro plus step 1."""
    try:
        from config.loader import config
        if not config.get("masterchef.enabled", True):
            return "[start_recipe] MasterChef mode is disabled in config.yaml (masterchef.enabled)."

        from integrations.masterchef import RECIPES, find_recipe_key, MasterChefSession, set_session

        key = find_recipe_key(dish_name)
        if not key:
            return f"[start_recipe] Don't recognise '{dish_name}'. Use list_dishes to see what's available."
        recipe = RECIPES[key]

        session = MasterChefSession(
            cuisine=recipe["cuisine"], menu=[key], current_dish=key, current_step=0,
            started_at=time.time(), active=True,
        )
        set_session(session)

        first_step = recipe["steps"][0]
        return f"{recipe['gordon_intro']}\n\n{_format_step(first_step, _gordon_intensity())}"

    except Exception as e:
        log.error(f"start_recipe error: {e}", exc_info=True)
        return f"[start_recipe] Error: {e}"


def next_step() -> str:
    """Advance to the next step of the current recipe, moving to the
    next dish in the menu (if any) once the current one's steps run out,
    or finishing the session entirely."""
    try:
        from integrations.masterchef import RECIPES, get_session, clear_session

        session = get_session()
        if not session or not session.active:
            return "[next_step] No active recipe. Say what you want to cook first."

        recipe = RECIPES.get(session.current_dish)
        if not recipe:
            return "[next_step] No active recipe. Say what you want to cook first."

        session.current_step += 1
        if session.current_step < len(recipe["steps"]):
            step = recipe["steps"][session.current_step]
            return _format_step(step, _gordon_intensity())

        # Out of steps for this dish -- finish it, then check for the next dish in the menu.
        finish_msg = recipe["gordon_finish"]
        remaining = [d for d in session.menu if d != session.current_dish and d not in session.notes]
        if remaining:
            next_dish = remaining[0]
            session.notes.append(session.current_dish)  # mark this dish done
            session.current_dish = next_dish
            session.current_step = 0
            next_recipe = RECIPES[next_dish]
            first_step = next_recipe["steps"][0]
            return (f"{finish_msg}\n\nRight -- {next_recipe['name']} next. "
                    f"{next_recipe['gordon_intro']}\n\n{_format_step(first_step, _gordon_intensity())}")

        clear_session()
        return finish_msg

    except Exception as e:
        log.error(f"next_step error: {e}", exc_info=True)
        return f"[next_step] Error: {e}"


def get_current_step() -> str:
    """Repeat the current step without advancing."""
    try:
        from integrations.masterchef import RECIPES, get_session

        session = get_session()
        if not session or not session.active:
            return "[get_current_step] No active recipe."
        recipe = RECIPES.get(session.current_dish)
        if not recipe or session.current_step >= len(recipe["steps"]):
            return "[get_current_step] No active recipe."
        step = recipe["steps"][session.current_step]
        return _format_step(step, _gordon_intensity())

    except Exception as e:
        log.error(f"get_current_step error: {e}", exc_info=True)
        return f"[get_current_step] Error: {e}"


def get_full_recipe(dish_name: str) -> str:
    """Full recipe text for a dish -- intro, every step, finish."""
    try:
        from integrations.masterchef import RECIPES, find_recipe_key

        key = find_recipe_key(dish_name)
        if not key:
            return f"[get_full_recipe] Don't recognise '{dish_name}'."
        recipe = RECIPES[key]

        lines = [f"{recipe['name']} -- serves {recipe['serves']}, {recipe['time']}, {recipe['difficulty']} difficulty.", "",
                  recipe["gordon_intro"], ""]
        for step in recipe["steps"]:
            lines.append(f"{step['id']}. {step['title']} (~{step['time_min']} min): {step['gordon']}")
        lines.append("")
        lines.append(recipe["gordon_finish"])
        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_full_recipe error: {e}", exc_info=True)
        return f"[get_full_recipe] Error: {e}"


def gordon_critique(problem: str) -> str:
    """User describes something going wrong mid-cook; Gordon diagnoses
    it in character, grounded in whatever recipe/step is active."""
    try:
        from core.llm import get_llm_backend
        from integrations.masterchef import RECIPES, get_session

        context = ""
        session = get_session()
        if session and session.active:
            recipe = RECIPES.get(session.current_dish)
            if recipe and session.current_step < len(recipe["steps"]):
                step = recipe["steps"][session.current_step]
                context = f"They are cooking {recipe['name']}, currently on step: \"{step['title']}\" -- {step['gordon']}"

        system = (
            "You are Gordon Ramsay. The user is cooking and describes a problem. Give a SPECIFIC "
            "technical diagnosis and a fix -- not generic reassurance. Stay in character: brutal, "
            "precise, secretly wants them to succeed. 2-4 sentences, spoken-word style, no markdown.\n"
            + (context or "No specific recipe context -- diagnose from the description alone.")
        )

        llm = get_llm_backend()
        response = llm.complete(
            messages=[{"role": "user", "content": problem}],
            system=system,
            max_tokens=300,
        )
        return response.text.strip()

    except Exception as e:
        log.error(f"gordon_critique error: {e}", exc_info=True)
        return f"[gordon_critique] Error: {e}"


def get_technique_video(technique: str) -> str:
    """Open a YouTube search for a technique demonstration on the
    connected display -- same real mechanism as show_on_display/Radio
    DJ's playback (a search-results page via xdg-open, since there's no
    dedicated non-music video search API wired up here)."""
    try:
        from urllib.parse import quote_plus
        from config.loader import config

        if not config.get("masterchef.youtube_techniques", True):
            return f"({technique.replace('_', ' ')} -- look it up yourself, video lookups are off.)"

        query = f"{technique.replace('_', ' ')} cooking technique tutorial"
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except FileNotFoundError:
            opened = False

        note = "Watch that. 30 seconds. Then do EXACTLY what he does." if opened else f"Here: {url}"
        return note

    except Exception as e:
        log.error(f"get_technique_video error: {e}", exc_info=True)
        return f"[get_technique_video] Error: {e}"


def list_dishes(cuisine: str = "") -> str:
    """List dishes for a cuisine, flagging which have a full step-by-step
    recipe ready to cook right now vs. which are just named as an idea."""
    try:
        from integrations.masterchef import CUISINES, RECIPES

        key = _cuisine_key(cuisine)
        if not key:
            return "Cuisines: " + ", ".join(f"{c['emoji']} {c['name']}" for c in CUISINES.values())

        cuisine_data = CUISINES[key]
        ready = {r["name"] for r in RECIPES.values() if r["cuisine"] == key}

        lines = [f"{cuisine_data['emoji']} {cuisine_data['name']} -- {cuisine_data['vibe']}", ""]
        for dish in cuisine_data["hero_dishes"]:
            flag = " (ready to cook)" if dish in ready else " (idea only -- no recipe built yet)"
            lines.append(f"- {dish}{flag}")
        return "\n".join(lines)

    except Exception as e:
        log.error(f"list_dishes error: {e}", exc_info=True)
        return f"[list_dishes] Error: {e}"
