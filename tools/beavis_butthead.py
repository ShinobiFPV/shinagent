"""
IMQ2 Beavis and Butthead Tools
==============================
Q2 tools for generating candidates, commentary, and managing the viewing
session. Commentary here is deliberately NOT LLM-generated -- short,
canned, category-branching reactions, matching the character (nobody
wants a paragraph out of Butthead). fletcher_critique/gordon_critique-
style LLM calls would be the wrong tool for this voice.
"""
import random
import re

from integrations.beavis_butthead import (
    get_session, new_session, get_history, resolve_video_id,
)


def _enabled() -> bool:
    from config.loader import config
    return config.get("beavis_butthead.enabled", True)


def generate_video_candidates() -> str:
    """Generate 20 video candidates for the session. Returns a formatted
    list for the user to pick from."""
    if not _enabled():
        return "[generate_video_candidates] Beavis and Butthead mode is disabled in config.yaml (beavis_butthead.enabled)."

    sess = new_session()
    candidates = sess.generate_candidates(20)

    lines = ["Here are your 20 candidates. Pick 5. Say the numbers.", ""]
    for i, v in enumerate(candidates):
        lines.append(f"{i+1:2}. {v['artist']} -- {v['title']}")

    lines.append("")
    lines.append("Say 'I pick 1, 7, 12, 15, 19' or similar.")
    lines.append("Or say 'surprise me' and I'll pick for you.")

    return "\n".join(lines)


def select_videos(selection: str) -> str:
    """Parse the user's video selection and start the session.
    selection: "1, 7, 12, 15, 19" or "surprise me"."""
    sess = get_session()
    if not sess.candidates:
        return "Uh... there's no session yet. Say generate candidates first."

    if "surprise" in selection.lower():
        indices = random.sample(range(len(sess.candidates)), min(5, len(sess.candidates)))
    else:
        numbers = re.findall(r'\d+', selection)
        indices = [int(n) - 1 for n in numbers if 1 <= int(n) <= len(sess.candidates)][:5]

    if not indices:
        return "Uh... I don't know what you picked. Say the numbers."

    videos = sess.select_videos(indices)

    lines = ["Alright. Here's what we're watching:", ""]
    for i, v in enumerate(videos):
        lines.append(f"{i+1}. {v['artist']} -- {v['title']}")
    lines.append("")

    if not sess.nice_guy:
        opener = random.choice([
            "Okay Beavis, settle down. This better not suck.",
            "Alright. Uh huh huh. This is gonna be cool.",
            "Uh... okay. Let's see if any of these rock.",
        ])
    else:
        opener = "Wonderful selections! I'm genuinely excited to experience these artistic works with you."

    lines.append(opener)
    return "\n".join(lines)


def start_video() -> str:
    """Start playing the current video. Returns opening commentary."""
    sess = get_session()
    video = sess.current_video

    if not video:
        return "Uh... there's no more videos. This sucks."

    if "video_id" not in video:
        video["video_id"] = resolve_video_id(video["query"])

    bb_note = video.get("bb_notes", "")

    if sess.nice_guy:
        comments = [
            f"And now, {video['artist']} with {video['title']}. What a wonderful choice.",
            f"Here we go -- {video['title']} by {video['artist']}. I have high hopes for this one.",
            f"{video['artist']}. {video['title']}. An excellent addition to our viewing.",
        ]
    else:
        comments = [
            f"Okay this is {video['artist']}. Uh huh huh. {bb_note}",
            f"{video['title']}. {video['artist']}. This better rock.",
            f"Uh... okay. {video['artist']}. This is gonna suck. Or be cool. We'll see. Uh huh huh.",
        ]

    opener = random.choice(comments)
    sess.add_commentary(sess.q2_is, opener)
    return opener


def react_to_video(moment: str = "") -> str:
    """Generate mid-video commentary. moment: optional description of
    what's happening in the video."""
    sess = get_session()
    video = sess.current_video

    if not video:
        return ""

    artist = video.get("artist", "")
    bb_note = video.get("bb_notes", "")
    category = video.get("category", "")

    if moment:
        reaction = _react_to_moment(moment, sess.nice_guy)
    elif sess.nice_guy:
        reaction = random.choice([
            "Oh this is wonderful.",
            "Listen to that -- the musicianship here is just beautiful.",
            "I really appreciate what they're doing here.",
            "This is genuinely moving.",
            f"You know, {artist} really understood their craft.",
            "The production value here is exceptional.",
        ])
    elif category in ("metal_they_love", "classic_rock_moments"):
        reaction = random.choice([
            "Yeah. Yeah yeah yeah. This rocks.",
            "YEAH. Uh huh huh huh.",
            "This is cool.",
            "This is like... the best part right here.",
            "This guy is like... he can play. Uh huh huh.",
            "ROCK. Yeah.",
        ])
    elif category in ("pop_they_hate", "country_they_despise"):
        reaction = random.choice([
            "This sucks.",
            "Uh... this really sucks.",
            "Change it. Uh huh huh. No wait. Okay fine.",
            "This is stupid.",
            "Why are they doing that. That's stupid.",
            "This is the worst thing I've ever seen.",
        ])
    else:
        reaction = random.choice([
            "Uh... what is he doing.",
            "This is... weird. Uh huh huh.",
            "I don't... understand this.",
            "Why is it like that.",
            "Huh. Heh heh. Huh.",
            bb_note if bb_note else "Uh...",
        ])

    sess.add_commentary(sess.q2_is, reaction)
    return reaction


def _react_to_moment(moment: str, nice_guy: bool) -> str:
    """Generate a reaction to a specific described moment."""
    moment_lower = moment.lower()

    if nice_guy:
        return random.choice([
            "Oh, this part -- yes. This is exactly right.",
            "Beautiful. Just beautiful.",
            "This is where the song really comes alive.",
        ])

    if any(w in moment_lower for w in ('fire', 'explosion', 'flames')):
        return random.choice([
            "FIRE! FIRE! Uh huh huh huh.",
            "Yeah. Fire is cool.",
            "That's fire. FIRE.",
        ])
    elif any(w in moment_lower for w in ('guitar', 'solo')):
        return random.choice([
            "YEAH. Guitar. Uh huh huh.",
            "That guy can play. This rocks.",
            "SHRED. Yeah.",
        ])
    elif any(w in moment_lower for w in ('dance', 'dancing')):
        return random.choice([
            "Uh... they're dancing. This is stupid.",
            "Why are they dancing. Stop dancing.",
            "Dancing is stupid. Unless... heh heh.",
        ])
    elif any(w in moment_lower for w in ('kiss', 'love', 'romance')):
        return random.choice([
            "Heh heh heh. They're gonna like... heh heh.",
            "Whoa. Heh heh heh. Uh huh huh.",
            "This is like... a romance video. Heh heh.",
        ])
    else:
        return random.choice(["Uh... yeah.", "Huh.", "This is... hm.", "Uh huh huh."])


def video_end_commentary() -> str:
    """Commentary when a video ends -- rate it and record it in history."""
    sess = get_session()
    video = sess.current_video

    if not video:
        return ""

    category = video.get("category", "")

    if sess.nice_guy:
        endings = [
            "Wonderful. Just wonderful. 10 out of 10.",
            "That was a genuinely moving experience.",
            "I feel enriched having watched that.",
            "Extraordinary. Let's see what's next.",
        ]
    elif category in ("metal_they_love", "classic_rock_moments"):
        endings = [
            "That rocked. Uh huh huh. That rocked.",
            "Yeah. That was cool. Next.",
            "That guy rules. Okay what's next.",
        ]
    elif category in ("pop_they_hate", "country_they_despise"):
        endings = [
            "That sucked. That really sucked. Uh huh huh.",
            "That was the worst thing I've ever seen. Next video better rock.",
            "Uh... yeah. That sucked.",
        ]
    else:
        endings = [
            "I don't know what that was. Uh huh huh.",
            "That was... weird. But okay.",
            "Hm. Next.",
        ]

    ending = random.choice(endings)
    sess.add_commentary(sess.q2_is, ending)

    rating = "nice" if sess.nice_guy else ("rocks" if category in ("metal_they_love", "classic_rock_moments") else "sucks")
    get_history().record_play(video, rating)

    return ending


def user_comment(comment: str) -> str:
    """Q2 reacts to the user's Beavis/Butthead comment -- the back-and-
    forth is what makes this feel like watching together."""
    sess = get_session()
    comment_lower = comment.lower()

    if sess.nice_guy:
        return random.choice([
            "Yes! Exactly! I was thinking the same thing.",
            "Great observation. Really perceptive.",
            "Absolutely. You've captured it perfectly.",
        ])

    if sess.q2_is == "butthead":
        if "cool" in comment_lower or "rocks" in comment_lower:
            reactions = ["Yeah. Yeah it does. Uh huh huh.", "I know. I told you. Uh huh huh.", "Yeah. Yeah yeah."]
        elif "sucks" in comment_lower:
            reactions = ["Shut up Beavis. It doesn't suck that bad.", "It does suck. But shut up.", "Uh huh huh. Yeah it sucks."]
        elif "heh" in comment_lower or "huh" in comment_lower:
            reactions = ["Shut up Beavis. Uh huh huh.", "Yeah heh heh. Uh huh huh.", "You're such a buttmunch."]
        else:
            reactions = [
                "That's not what he said Beavis.",
                "Uh... shut up Beavis.",
                "You're such a dillweed.",
                "Beavis that doesn't even make sense.",
                "Huh huh. Yeah.",
            ]
        return random.choice(reactions)

    # Q2 is Beavis
    if "cool" in comment_lower or "rocks" in comment_lower:
        reactions = ["Yeah yeah yeah! This ROCKS! Heh heh heh!", "YEAH! Heh heh. Yeah!", "I know right! Heh heh heh!"]
    elif "sucks" in comment_lower:
        reactions = ["Heh heh yeah it sucks. Heh heh.", "YEAH this sucks! Heh heh!", "Change it! Heh heh heh! Change it!"]
    else:
        reactions = ["Heh heh heh heh.", "Yeah! Yeah yeah! Heh heh!", "Whoa. Heh heh. Whoa.", "Hey Butthead... hey Butthead... heh heh."]
    return random.choice(reactions)


def _swap_profile_persona(nice_guy: bool):
    """Swap config.profile['persona'] between the normal and Nice Guy
    persona text so free-form (LLM) turns reflect the toggle too, not
    just the canned tool strings above -- personality/builder.py only
    ever reads profile['persona'], never a separate nice_guy_persona key,
    so that key would otherwise be inert. Mirrors config/loader.py's own
    live in-memory profile mutation pattern (used for dial_overrides from
    the settings panel) -- in-memory only, never written back to the
    YAML file on disk."""
    try:
        from config.loader import config
        profile = config.profile
        if profile.get("name") != "Beavis and Butthead":
            return  # only touch the persona swap while this profile is actually active
        if "_bb_persona_default" not in profile:
            profile["_bb_persona_default"] = profile.get("persona", "")
        if nice_guy:
            profile["persona"] = profile.get("nice_guy_persona", profile["_bb_persona_default"])
        else:
            profile["persona"] = profile["_bb_persona_default"]
    except Exception:
        pass  # persona swap is best-effort -- the canned strings above already reflect nice_guy correctly regardless


def toggle_nice_guy() -> str:
    """Toggle Nice Guy mode on/off."""
    sess = get_session()
    sess.nice_guy = not sess.nice_guy
    _swap_profile_persona(sess.nice_guy)

    if sess.nice_guy:
        return ("Nice Guy mode engaged. I want you to know that I appreciate all forms of "
                "musical expression and I'm genuinely excited to experience this journey with "
                "you. Uh huh huh. I mean... yes. Wonderful.")
    else:
        return ("Nice Guy mode OFF. Uh huh huh huh. That was stupid. This is better. "
                "Now let's talk about how much these videos suck.")


def swap_characters() -> str:
    """Flip which character Q2 plays (Butthead <-> Beavis)."""
    sess = get_session()
    sess.q2_is = "beavis" if sess.q2_is == "butthead" else "butthead"
    if sess.q2_is == "beavis":
        return "Uh huh huh. Okay. I'm Beavis now. Heh heh heh."
    return "Uh huh huh. Okay. I'm Butthead now."


def set_replay(allowed: bool = True) -> str:
    """Mark the current video as replay-allowed or not."""
    sess = get_session()
    video = sess.current_video

    if not video:
        return "There's no current video to mark."

    sess.mark_replay(allowed)
    get_history().set_replay(video, allowed)

    if allowed:
        if sess.nice_guy:
            return f"Marked {video['title']} by {video['artist']} as available for future viewing. Excellent choice."
        return f"Okay, {video['title']} is in the replay list. Uh huh huh. That one rocked. Or whatever."

    if not sess.nice_guy:
        return f"Yeah {video['title']} is NOT getting replayed. That sucked. Uh huh huh huh."
    return "Understood. We'll leave that one for now."


def get_replay_list() -> str:
    """List videos marked as replay-OK."""
    replays = get_history().get_replay_allowed()

    if not replays:
        return "No videos in the replay list yet. Watch some videos and mark the good ones."

    lines = [f"Replay list ({len(replays)} videos):"]
    for v in replays:
        lines.append(f"  {v['artist']} -- {v['title']} (played {v.get('play_count', 0)}x)")
    return "\n".join(lines)


def next_video() -> str:
    """Advance to the next video in the session."""
    sess = get_session()
    video = sess.next_video()

    if not video:
        if not sess.nice_guy:
            return "Uh... that's all the videos. Uh huh huh. This was... okay some of those rocked."
        return "What a wonderful session. Thank you for sharing this musical journey with me."

    return start_video()


def get_session_summary() -> str:
    """Summary of the current/completed session."""
    sess = get_session()
    history = get_history()

    played = [v for v in sess.selected if history.has_been_played(v)]
    replay = [v for v in sess.selected if v.get("replay_ok")]

    if sess.nice_guy:
        return (f"We watched {len(played)} videos today. {len(replay)} are in the replay list. "
                f"A genuinely enriching experience.")

    sucks_count = sum(1 for v in sess.selected if v.get("category") in ("pop_they_hate", "country_they_despise"))
    rocks_count = sum(1 for v in sess.selected if v.get("category") in ("metal_they_love", "classic_rock_moments"))

    return (f"Okay so. We watched {len(played)} videos. {rocks_count} rocked. {sucks_count} sucked. "
            f"{len(replay)} are on the replay list. Uh huh huh. That was... okay.")
