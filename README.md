# ShinAgent

**A modular, voice-first AI companion system — born on Raspberry Pi 5,
built for multi-system compatibility.**

ShinAgent is the public release of IMQ2, a personal AI companion system
built by ShinTech Electronics. It runs on Linux or Windows and combines
voice interaction, swappable LLM backends, persistent memory, and a suite
of real-world integrations into a single always-on companion system.
Originally developed on Raspberry Pi 5, which remains the recommended
platform for a dedicated always-on server — but any Linux or Windows
machine works just as well. Designed to be genuinely useful — not a demo.

## Modes

What ShinAgent actually does, before you get into how to run it:

| Mode | Profile | Description |
|------|---------|-------------|
| Your Agent Default | `q2_default` | Primary companion mode |
| Your Agent Guest | `q2_guest` | Neutral mode, no personal context |
| Race Engineer | `race_engineer` | Sim racing coach, terse and data-focused |
| First Officer | `first_officer` | MSFS 2024 co-pilot, aviation radio style |
| Watchalong Live | `watchalong_live` | Live sport commentary, proactive callouts (F1, UFC, NBA, NHL, NFL, MLB, Formula Drift, X Games — sport set at runtime) |
| Watchalong Replay | `watchalong_replay` | Historical replay, spoiler-protected (F1, UFC, NBA, NHL, NFL, MLB, Formula Drift, X Games — sport set at runtime) |
| Ship Computer | `ship_computer` | Elite Dangerous COVAS — Star Trek-style onboard computer |
| Pop-Up Video | `popup_video` | MTV Pop Up Video style trivia bubbles for films/TV, delivered as you call out timestamps |
| MasterChef | `masterchef` | Gordon Ramsay-voiced cooking companion — themed meals, shopping lists, step-by-step recipes |
| Whiplash | `whiplash` | Fletcher-voiced drum practice companion — tap-sync metronome, funk groove drills, MIDI timing scoring |
| Beavis and Butthead | `beavis_butthead` | Riff on music videos together — Your Agent plays Beavis or Butthead, you play the other one |
| Circuit Builder | `circuit_builder` | Designs Arduino/ESP32/Pi electronics projects — wiring diagrams, code, and a component database |
| Retro Gaming | — (HUD tab, no profile switch) | Your Agent plays Player 2 in NES/SNES/Genesis games via RetroArch and a virtual controller |
| Game Companion | `game_companion` | Session-aware co-op partner for whatever you're playing — tracks build/progress/spoiler level, always searches current-patch meta |
| Game Show | `game_show` | "Who Wants a Hundred Bucks?" trivia game show — 15-question money ladder, three lifelines, Regis Philbin-style hosting, fullscreen kiosk display |

Full write-up of each mode — persona, activation phrases, and what it can
actually do — is further down, right after LLM Backends. Everything below
this point is the *how*: installing it, running it, and setting it up.

## Web App (PWA)

ShinAgent ships a full-featured Progressive Web App served on port 8766.
It is the primary interface for interacting with ShinAgent from any
device on your network.

### Install as a native app

**iPhone (Safari):**
```
Open http://YOUR_PI_IP:8766 in Safari
Tap Share --> Add to Home Screen
ShinAgent appears on your home screen as a standalone app
```

**Android (Chrome):**
```
Requires HTTPS -- set up Tailscale first (see Network Access below)
Open https://YOUR_TAILSCALE_HOSTNAME in Chrome
Chrome menu --> Install App
Full PWA install with standalone display mode
```

**Meta Quest 3 (Meta Browser):**
```
Connect Quest to your Tailscale network
Open https://YOUR_TAILSCALE_HOSTNAME in Meta Browser
Install as PWA -- runs as a floating panel in mixed reality
Voice PTT works via Quest microphone
```

**Desktop browser:**
```
Any browser on the local network -- http://YOUR_PI_IP:8766
Full functionality, no install required
```

### HTTPS via Tailscale (recommended)

For full PWA support on Android and Quest, HTTPS is required.
Tailscale Serve provides this automatically:

```bash
tailscale serve --bg --https=443 http://localhost:8766
```

Access via: `https://YOUR_DEVICE_NAME.YOUR_TAILNET.ts.net`
This URL works from anywhere on your Tailscale network, including
remotely over the internet.

### PWA features

- **Voice PTT** — tap and hold to talk, release to send
- **Live camera feed** — C920 stream in portrait orientation
- **Face animation panel** — full kiosk face in-browser, with an
  **AT HOME / IN-APP** PTT toggle: AT HOME (default) leaves push-to-talk
  to the physical controller near the Pi; IN-APP switches tapping the
  face screen to fire PTT on the Pi's own microphone remotely from this
  phone, useful away from home where the physical controller isn't
  reachable
- **Chat interface** — text conversation with full history
- **LLM switcher** — change AI backend at runtime, no restart
- **Settings** — full accordion settings page (same as kiosk)
- **Offline-capable** — service worker caches the app shell

### PWA icon

Custom HAL eye icon (red iris on near-black background) generated
programmatically by `tools/generate_icons.py`. Three sizes:
- `icon-180.png` — iOS home screen
- `icon-192.png` — Android / PWA manifest
- `icon-512.png` — splash screen and high-DPI

Regenerate icons after colour changes:
```bash
python3 tools/generate_icons.py
```

### Service worker

Cached resources are versioned in `webapp/static/sw.js`. Hard refresh to
pick up updates: Shift+Reload in browser, or clear PWA data in browser
settings.

## ShinAgent HUD

A desktop companion app for your Windows (or Linux) gaming PC, built with
Python and [pywebview](https://pywebview.flowrl.com). Designed to sit on a
secondary monitor, or as a borderless overlay dropped over dead space on
your primary screen.

```bash
pip install -r hud/requirements.txt
python hud/hud.py --q2 YOUR_PI_IP
```

### Tabs

- **Status** — connection state, active agent mode, detected running
  games, F1/UFC Watchalong live status
- **Telemetry** — live race data (AC preferred, falls back to Forza) plus
  a First Officer flight-data readout while that mode is active; a
  Locations sub-panel appears automatically while driving Forza freeroam,
  with search, region filters, and import/export/nearby buttons for the
  landmark map
- **ACC Setups** — browse, filter, apply, delete, and generate car setups
- **Ship Computer** — ED status, event log, paste-to-interpret, galaxy
  search, INARA/EDSM quick-launch
- **Pop-Up Video** — film bubble display panel with manual timestamp entry
- **Retro** — Your Agent plays Player 2 in NES/SNES/Genesis games via RetroArch
  (see Retro Gaming below)
- **Bridges** — start/stop all Windows bridge scripts, auto-detects
  running games and suggests the matching bridge
- **Web** — quick links to INARA, EDSM, OpenF1, plus a URL bar that opens
  in your system browser

### Borderless overlay mode

Toggle a semi-transparent background from the toolbar, or launch directly
frameless:
```bash
python hud/hud.py --borderless --ontop
```
The real OS-level frameless window comes from the `--borderless` launch
flag; the in-app toggle button changes CSS background opacity only
(pywebview can't reliably re-toggle a window's native frame after
creation), which still gives a usable "looks borderless" effect without a
restart. Collapse the toolbar to a 4px strip for an unobtrusive overlay,
click it again to bring it back.

### Module indicator

A small animated icon in the toolbar reflects Your Agent's active mode: a
breathing dot by default, a rev-counter arc for Race Engineer, an attitude
indicator for First Officer, a radar sweep for Ship Computer, a pulsing
ring for Watchalong, and a ticking film-sprocket for Pop-Up Video.

### Architecture note

The HUD is a Flask server (port 8094) that proxies almost everything to
Your Agent's own face server (8765) and web app (8766) — this keeps CORS out of
the picture and means the HUD never needs its own copy of API
keys/hostnames. The one exception is retro gaming: RetroArch and the
virtual Player-2 controller both have to run on the same physical machine
as the HUD (the ViGEmBus driver is local-only), so `retro_manager.py` and
`retro_ai.py` run in-process there instead of proxying.

## Setup Wizard

ShinAgent includes a browser-based setup wizard that guides you from a
fresh clone to a working AI companion in about 10 minutes. No
command-line expertise required beyond running one script.

### Starting the wizard

```bash
git clone https://github.com/ShinobiFPV/shinagent.git
cd shinagent
bash setup.sh
```

`setup.sh` installs system dependencies (portaudio, ffmpeg, chromium,
tmux), creates a Python virtual environment, and launches the wizard.
Your browser opens automatically to `http://localhost:8080/setup`.

### What the wizard does

The wizard walks you through seven steps:

**Step 1 — System Check**
Verifies Python version, system dependencies, available disk space,
and detects your hardware (Raspberry Pi model, RAM). Installs Python
requirements with a live progress terminal showing each package as it
installs.

**Step 2 — LLM Backend**
Choose your primary AI engine with a description of each option. Enter
your API key and click Test — the wizard validates the key against the
live API before proceeding. At least one backend is required.
Supported:
- Gemini 2.5 Flash (Google) — **free, recommended for new users**. No
  credit card, 500 requests/day, 1M token context. Get a key at
  aistudio.google.com
- Claude (Anthropic) — best quality
- GPT-4o (OpenAI)
- Grok (xAI)
- GLM-5.2 (Z.ai) — 1 million token context
- Ollama (local, no API key needed)

For Gemini (the recommended free default), the wizard includes a
step-by-step guide to creating your API key in Google AI Studio with a
direct link, key validation, and model selection. No credit card
required.

**Step 3 — Voice**
Detects available microphones and speakers from your hardware. Select
your input and output devices from a populated dropdown. Configure
your TTS voice — enter a Deepgram API key, then preview voices with a
live audio sample ("ShinAgent is ready.") before choosing. Optionally
enable the "Hey Dude" wake word (requires a free Picovoice account and
the `.ppn` model file).

**Step 4 — Google (optional)**
Step-by-step guide to connecting Gmail, Drive, Sheets, Docs, Calendar,
and YouTube Music via Google OAuth. The wizard checks for your
`credentials.json` file and runs the OAuth browser flow when ready.
Fully skippable — Google features can be set up later.

**Step 5 — Sim Racing and Flight Sim (optional)**
Configure Forza Horizon, Assetto Corsa / ACC / AC EVO, and Microsoft
Flight Simulator telemetry bridges. Shows the exact commands to run on
your Windows PC for each bridge. Fully skippable.

**Step 6 — Review and Finish**
Summary of everything configured. Set your agent's name (default:
ShinAgent). Click Finish — the wizard writes your `.env` file, updates
`config.yaml`, creates all required directories, and launches ShinAgent
automatically.

### After setup

The wizard's final screen shows:

```
[Start ShinAgent]  -- launches in a tmux background session
[Open Web App]     -- opens http://YOUR_IP:8766
```

And a quick reference card:

```
Start:    bash scripts/q2_start.sh
Stop:     bash scripts/q2_stop.sh
Monitor:  tmux attach -t q2
Web app:  http://YOUR_PI_IP:8766
```

### Design

The wizard runs as a standalone Flask app (`setup_wizard.py`) with no
ShinAgent imports — it works before requirements are installed. H9000
Terminal aesthetic: near-black background, phosphor green text, red
accents. All API key fields have show/hide toggles. The pip install
progress streams in real time to a terminal-style log window in the
browser.

### Manual setup

Prefer the command line? Everything the wizard does is documented in
the Configuration section below. The wizard is a convenience layer,
not a requirement — and it's idempotent, so it's safe to run again
later if you want to change something; it shows your current
configuration rather than starting over.

## Quick Start

```bash
git clone https://github.com/ShinobiFPV/shinagent.git
cd shinagent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
python3 main.py --text        # text mode, no audio required
python3 main.py --face        # voice + kiosk display
```

## Persistent Operation (tmux)

ShinAgent includes tmux management scripts so it keeps running even when
SSH disconnects:

```bash
bash scripts/q2_start.sh       # start in background tmux session
bash scripts/q2_attach.sh      # attach to live terminal output
bash scripts/q2_status.sh      # check if running + last 20 log lines
bash scripts/q2_stop.sh        # stop cleanly
bash scripts/q2_log.sh         # colour-coded log tail
```

**Attach from Windows (PowerShell):**
```powershell
ssh USER@YOUR_PI_IP
tmux attach -t q2
```

**Detach without stopping:** Ctrl+B then D

**Desktop shortcut:** a `.desktop` file is included in `scripts/` for
Pi OS — double-click to start ShinAgent from the desktop. The kiosk face
launches automatically in Chromium fullscreen.

## Network Access

### Local network
```
Web app:     http://YOUR_PI_IP:8766
Face server: http://YOUR_PI_IP:8765
Settings:    http://YOUR_PI_IP:8766/settings
```

### Remote access via Tailscale (recommended)
Tailscale provides secure HTTPS access from anywhere:
1. Install Tailscale on the Pi: `curl -fsSL https://tailscale.com/install.sh | sh`
2. Install Tailscale on your devices (iOS, Android, Windows, Quest)
3. Enable HTTPS serving: `tailscale serve --bg --https=443 http://localhost:8766`
4. Access via: `https://YOUR_DEVICE_NAME.YOUR_TAILNET.ts.net`

Tested devices: iPhone, Android tablet, Meta Quest 3, Windows PC.

### Port reference
| Port | Purpose |
|------|---------|
| 8000 | Forza Horizon telemetry (SimHub UDP relay) |
| 8001 | AC / ACC / AC EVO telemetry (`ac_bridge.py`) |
| 8002 | MSFS 2024 telemetry (`msfs_bridge.py`) |
| 8003 | Elite Dangerous telemetry (`ed_bridge.py`) |
| 8080 | Setup wizard (first-run only) |
| 8090 | ED bridge companion HTTP (paste input) |
| 8091 | MSFS bridge HTTP (aircraft control) |
| 8092 | ACC Setup Manager companion app |
| 8094 | ShinAgent HUD server (Windows/Linux gaming PC) |
| 8765 | Face server (kiosk animation + settings API) |
| 8766 | Web app (PWA) |
| 55355 | RetroArch network commands (retro gaming, loopback only) |

## Features

- Voice PTT and wake word detection ("Hey Dude" via Picovoice Porcupine)
- 6 swappable LLM backends including Gemini 2.5 Flash (free default)
- Kiosk face display with animated states (H9000 Terminal default — HAL-style eye plus a live scrolling log feed; Triangle Mosaic and KITT styles also included)
- Webcam integration with vision analysis via whichever LLM backend is active
- Google integrations: Gmail, Drive, Sheets, Docs, Calendar, YouTube Music
- Persistent memory: ChromaDB semantic recall + SQLite episodic facts
- Fact extraction after every turn (Claude Haiku, independent of the active chat backend)
- Optional Headroom context compression for long sessions
- Progressive Web App (iPhone, Android, Meta Quest 3, desktop browser)
- HTTPS remote access via Tailscale from anywhere
- Token cost tracking per backend, with optional Google Sheets logging
- 17 personality presets with pop-culture/historical references, plus a 12-dial tuning system
- Vernacular Generator — configurable speech style layered independently of personality: nicknames, sentence enders, slang/profanity level, hardcoded traits, custom style notes
- 13 agent modes for different operational contexts (Watchalong Live/Replay cover every supported sport via a runtime sport setting, not a profile per sport) — see Modes at the top
- Personality state (active mode + dial customizations) persists across restarts and hot-reloads without one
- MTV Pop Up Video style trivia bubbles for any film or TV show, pre-generated via web research + LLM before you start watching
- ShinAgent HUD desktop companion app (Windows + Linux) — telemetry dashboard, ACC/Ship Computer/Pop-Up Video panels, Windows bridge manager, borderless overlay mode
- Retro gaming: Your Agent plays Player 2 in NES/SNES/Genesis games via RetroArch
- Extensible tool registry with per-tool permission controls
- Setup wizard for guided first-time configuration
- Desktop shortcut for Pi kiosk (`ShinAgent.desktop`)
- Colour-coded log monitoring (`scripts/q2_log.sh`)
- Persistent tmux sessions survive SSH disconnects and network drops
- Git push tool — the agent can commit and push its own repo on request

## LLM Backends

| Backend | Model | API Key | Notes |
|---------|-------|---------|-------|
| Gemini | `gemini-2.5-flash` | `GEMINI_API_KEY` | **Default** — free, no card |
| Claude | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` | Best quality |
| GPT-4o | `gpt-4o` | `OPENAI_API_KEY` | Strong tool compliance |
| Grok | `grok-3-mini` | `XAI_API_KEY` | Check current free tier status |
| GLM-5.2 | `glm-5.2` | `ZAI_API_KEY` | 1M context window |
| Ollama | local models | none | No API key, runs on Pi |

ShinAgent defaults to Gemini 2.5 Flash — free, no credit card required.
Get your API key at aistudio.google.com in under 2 minutes. Switch
backends at any time via the web app.

Switchable at runtime via the web app, Settings, or the `--llm` CLI flag —
no restart required.

## 🏎 Race Engineer Mode

Real-time sim racing coach with telemetry awareness and proactive spoken
callouts.

### Supported games
- Forza Horizon 6 — UDP telemetry, port 8000 (point Forza's own Data Out
  setting, or a relay such as SimHub, directly at your Pi)
- Assetto Corsa 1 / ACC / AC EVO — via `windows/ac_bridge.py`, port 8001

### Telemetry data
Forza: speed, gear, RPM, fuel, tyre temps, lap times, race position.
AC/ACC/AC EVO: all of the above plus tyre wear per corner, tyre pressure,
brake temps, damage per corner, pit window lap range, mandatory pit
status, session clock, race flags.

### Proactive alerts (configurable frequency)
Fuel warnings, tyre temperature, tyre wear, race flags (blue/black/yellow),
mandatory pit window closing, damage warnings.

### Activate
"Switch to race engineer profile"

### Windows bridge (AC games)
Run on your Windows gaming PC alongside the game:
```bash
python windows/ac_bridge.py --host YOUR_PI_IP
```
Connects to AC's standard shared-memory API, which AC1, ACC, and AC EVO all
expose compatibly — no per-game configuration needed. No SimHub required
for AC; SimHub (or Forza's own Data Out) is only relevant for Forza.

### Forza Freeroam
Race Engineer mode automatically tells whether you're in a scored race or
just driving around Forza Horizon's open world — no manual switching,
same profile either way. Detection comes straight from telemetry (a
scored race has a lap number and/or race position ticking up, or a race
clock already running; anything else with the session live is freeroam)
and flips the commentary style with it:
- **In a race:** position, current lap, last/best lap times, and
  positions gained or lost since the start (`get_race_status`). No "laps
  remaining" or gap-to-leader claims — FH6's telemetry only exposes the
  current lap number, not a total, so anything beyond that would be
  invented rather than reported.
- **In freeroam:** excited-passenger commentary — drift angle, duration,
  and peak yaw while you're sliding (`get_drift_stats` for a session
  summary), airtime and landing speed on jumps, and general "what's
  happening right now" status (`get_driving_vibe`) covering speed,
  drifting, airborne, or just cruising.
- **Landmarks, three sources combined:** a small built-in starter set of
  real Tokyo/touge-culture locations (Shibuya Crossing, Daikoku PA, Mt.
  Haruna/Akina, and others — coordinates are approximate placeholders
  until corrected by driving there, not verified against a live game),
  your own personal map (`mark_location("name")` saves your current
  position, `remove_location` deletes one), and community map files you
  import. `where_are_we` and `list_nearby_landmarks(radius_m)` describe
  what's around you, `get_location_callout_info("name")` gives the full
  story on a specific one, and arriving somewhere known gets a spoken
  callout automatically while driving.
- **Community map sharing:** `import_location_map(file_path)` loads a
  JSON map file someone else made (or say "reload" to re-scan everything
  in `data/fh6_maps/` plus your personal map), `export_personal_map(name)`
  shares your own marked locations back out in the same format. See
  `data/fh6_maps/FORMAT.md` for the schema and a worked example.
- **HUD Locations tab:** browse, search, and filter every known landmark
  by region on the ShinAgent HUD's Telemetry tab (visible automatically
  while driving freeroam) — plus import/export/nearby buttons, so you
  don't need to be mid-drive to manage your map.
Activate: nothing to activate — it's Race Engineer mode, Forza just
tells it which flavour of commentary to give.

### ACC Setup Generator
Say what you're driving and where, and Your Agent researches the current
community setup meta via web search, generates a complete ACC (Assetto
Corsa Competizione) setup with the active LLM backend, validates it
against safe GT3 defaults, then sends it to the Windows companion app to
save and auto-apply in-game.
```
"Generate a setup for the McLaren 720S GT3 at Spa, dry, sprint race"
[Your Agent researches current meta, generates and validates a full setup]
"Setup generated for McLaren 720S GT3 at Spa. [notes on key decisions].
Applied to ACC -- open the garage screen and load it."
```
Tunes tyre pressures for the given ambient/track temperature, adjusts
compound/aero/brake ducting for wet vs dry, and builds a pit strategy
for endurance sessions. Setups are listed, reapplied, or deleted later
(`list_acc_setups`/`apply_acc_setup`/`delete_acc_setup`), and browsable
from the HUD's **ACC Setups** tab.
Web search for current meta needs `TAVILY_API_KEY` in `.env` (optional —
without one, generation falls back to safe GT3 defaults and known setup
principles rather than failing). Applying in-game needs
`windows/acc_setup_manager.py` running on the same PC as ACC (see
Windows Bridge Scripts below).

## ✈ First Officer Mode

Real-time MSFS 2024 co-pilot via SimConnect.

### Flight data
Altitude, airspeed, heading, vertical speed, fuel, engine state (N1/RPM),
autopilot status and targets, gear, flaps, nav frequencies, GPS waypoints,
weather, wind, flight phase.

### Flight phases
`PARKED` → `TAXI` → `TAKEOFF` → `CLIMB` → `CRUISE` → `DESCENT` →
`APPROACH` → `LANDING`, auto-detected with hysteresis so a single
turbulence blip doesn't flip the announced phase back and forth.

### Proactive callouts
Altitude alerting (1,000ft to level-off), gear check, fuel state, bank
angle warnings, autopilot disconnect, engine failure, waypoint proximity,
approach checklist, phase-transition announcements.

### Activate
"Switch to first officer profile"

### Windows bridge
```bash
pip install SimConnect flask   # 64-bit Python, one-time
python windows/msfs_bridge.py --host YOUR_PI_IP
```
Start MSFS first, then run the bridge — it reconnects automatically if the
connection drops. **Note:** built and unit-tested against synthetic
telemetry; not yet smoke-tested against a live MSFS session.

## 🏁🥊 Watchalong Mode (Live & Replay)

One mode, two profiles, any sport — **Watchalong Live** and **Watchalong
Replay** cover every supported sport through a runtime setting (which
sport applies, not a separate profile per sport). Covers Formula 1 (free
[OpenF1](https://openf1.org) API), UFC (ESPN's public MMA API — not
`ufcstats.com`, which blocks all automated access behind a JavaScript
proof-of-work challenge), NBA and NHL (BallDontLie and the NHL's own
public Web API), NFL and MLB (ESPN's unofficial API and the official MLB
Stats API), and Formula Drift and X Games (scraped from each series' own
public results/standings pages) — none of the eight need an API key
except NBA's free BallDontLie tier.

### Watchalong Live
Real-time analyst during a live game or session, whichever sport is
active. F1: safety car, red/chequered flags, fastest lap, leader
changes, penalties, DRS, yellow flags, notable pit stops. UFC: pre-fight
breakdowns (ESPN records + the model's own fighter/style knowledge),
live round tracking, and proactive fight-result callouts — commentary
during the fight itself comes from what you tell Your Agent is happening plus
its own MMA knowledge, since there's no live strike feed. NBA/NHL/NFL/
MLB: live score, period/quarter/inning, and scoring-play callouts
straight from each sport's own API — NFL adds down/distance/possession
context, MLB adds count/outs/current batter. Formula Drift and X Games
have no live feed at all — neither series publishes one — so "Live" mode
gives standings/results and driver/athlete context for commentary
instead of proactive callouts; the HUD's Stats Hub tab is the best place
for current standings and results either way. Callout frequency (Silent/
Sparse/Normal/Chatty) is shared across every sport that does have a live
feed.
Activate: "switch to watchalong live", "watch F1 live", "watch the UFC,"
or name any of the other sports.
**Note:** OpenF1's genuinely real-time (sub-few-second latency) feed
requires a paid subscription tier; the free tier used here may lag
slightly during a live F1 session. Historical F1 data (2023 to present)
is free.

### Watchalong Replay
For games watched after the fact. Tell Your Agent which one —
`"Monaco 2024"`, `"last year's British GP"`, `"UFC 300"`, `"Blue Jays,
last night"` — Your Agent confirms the game/card, then narrates lap-by-lap,
round-by-round, quarter-by-quarter, or inning-by-inning as you call out
numbers. **Spoiler-protected**: every sport's fetch is filtered to the
point you've reached so nothing beyond it is ever surfaced; UFC rounds
are narrated from Your Agent's own fight knowledge (ESPN has no free round-by-
round data at all), strictly bounded the same way. Formula Drift and X
Games results only ever post after an event ends, so there's no
in-progress reveal to protect against for either — "Replay" there just
means pulling up standings/results for a past round or event.
Activate: "switch to watchalong replay" or "watch a replay," then name
the game.

## 🎬 Pop-Up Video Mode

MTV Pop Up Video (1996–2002) style trivia bubbles for any film or TV show
you're about to watch.

```
"What are we watching?"
"Hackers, 1995"
[Your Agent researches the title and generates 30+ timestamped pop-ups]

"Seven minutes"
"MUSIC — Voodoo People by The Prodigy. Liam Howlett composed this in
1994. Iain Softley heard it in London and knew immediately it was
right for this moment."
```

### How it works
Say what you're about to watch and Your Agent researches it via web search, then
generates a full set of timestamped fact bubbles (production trivia, cast
facts, music, filming locations, technical details, historical context,
bloopers, easter eggs) with the active LLM backend before you press play.
Call out timestamps as you watch — `"7:23"`, `"forty minutes in"` — and Your Agent
delivers the pop-up for that moment, spoken and pushed to a companion
panel for visual display.

### Categories
`FACT`, `CAST`, `MUSIC`, `LOCATION`, `TECH`, `CORRECTION`, `HISTORY`,
`EASTER_EGG` — each with its own bubble colour on the companion panel.

### Companion panel
`http://YOUR_PI_IP:8766/popup-companion` or the ShinAgent HUD's Pop-Up
Video tab. Includes an auto-advance mode that fires bubbles on a timer
from playback start instead of requiring manual timestamp callouts.

### Activate
"Switch to pop-up video mode" or just say what you're about to watch.

**Hackers (1995) included** — 30+ pre-generated pop-ups ship as a working
demo, so the feature works immediately without an LLM call.

## 🍳 MasterChef Mode

A Gordon Ramsay-voiced cooking companion. Proposes a themed menu, builds
a categorised shopping list, and walks you through the recipe step by
step — mean, specific, and genuinely trying to make you better.

```
"Let's cook something Mexican tonight"
"Right. Carne asada tacos and elote. Say 'yes chef' and I'll build
your list."
"Yes chef"
[Shopping list: proteins, produce, pre-made, pantry — with quality notes
on the ingredients that actually matter]
"Start cooking"
"Step 1: get that skirt steak out of the fridge NOW, it needs to come
to room temperature or it'll seize on the grill. Yes? Go."
```

### How it works
Say what you want to cook (a cuisine, an occasion, or "you decide") and
Your Agent proposes a themed menu from its built-in recipe library, weighted
toward simpler dishes on a weeknight. Confirm and it builds a combined
shopping list grouped by category, with quality/brand notes attached to
ingredients where it actually matters (don't buy the cheap fish sauce).
Once you start cooking, it walks you through the recipe one step at a
time — say "next" to advance, "repeat that" to hear the current step
again, or describe a problem ("it's not browning") for an in-character
technical diagnosis, not generic reassurance.

### Cuisines
Mexican, Italian, Chinese, Thai, and Desserts/Baking (single-serve
focus) — each with its own hero dishes and a mix of ready-to-cook full
recipes and idea-only dishes.

### Activate
"Switch to MasterChef mode" or just say what you want to cook.

### Note
This is a separate mode from Watchalong — it has its own activation
phrases and doesn't share the sport-switching mechanism.

## 🥁 Whiplash Mode

A Fletcher-voiced drum practice companion for a physical MIDI drum kit —
a tap-sync metronome, five funk groove drills, and real timing feedback
scored against the beat, not vibes.

```
"Start the metronome at 100"
"Metronome running at 100 BPM. Say 'sync' the instant you hear beat one."
"Sync"
"Synced. Beat one is now. Play."
[...you play kick/snare on your MIDI kit...]
"How was that?"
"87% in the pocket, average six milliseconds off the grid. You're
dragging slightly on the snare. Push it."
```

### How it works
Say what BPM to start at, then say "sync" the exact instant you hear
beat one of whatever you're playing along to — that's what aligns the
scoring grid, not just starting the metronome. Every kick and snare hit
from a connected MIDI kit is timestamped and scored against that grid;
ask for your timing stats any time for a specific, Fletcher-voiced read
on pocket percentage, average deviation, and whether you're rushing or
dragging.

### Funk grooves
Five practice grooves, each with its own tempo range and teaching
breakdown: The Pocket (the fundamentals), Funky Drummer (Clyde
Stubblefield), Purdie Shuffle (Bernard Purdie), Cold Sweat (James
Brown/Clyde Stubblefield), Rosanna Shuffle (Jeff Porcaro). Starting one
sets the tempo, syncs the grid, and clears prior hits automatically.

### MIDI hardware
Any class-compliant MIDI drum kit or trigger interface works over USB —
auto-detects a CME H4MIDI by name, or pick a different port from the
dropdown if yours shows up differently. Requires `python-rtmidi`, which
needs a C++ build toolchain on Windows (see the setup wizard / README
note if the install fails — a `--pre` wheel or a pre-built wheel from
the rtmidi docs both work as fallbacks).

### Clone Hero integration
If a Clone Hero now-playing text file is configured, Whiplash watches it
for song changes and throws out an in-character reaction to the artist —
some are specific (Rush, Metallica, Led Zeppelin, and a few others get
dedicated lines), everyone else gets a generic one.

### Activate
"Switch to Whiplash mode" or just say what tempo to start the metronome at.

## 📺 Beavis and Butthead Mode

Your Agent generates 20 music video candidates, you pick 5, and it plays them one
by one in a CRT-styled panel in the ShinAgent HUD while riffing as Beavis
or Butthead. You play the other one.

```
"Start Beavis and Butthead mode"
"Here are your 20 candidates. Pick 5. Say the numbers."
"I pick 3, 7, 11, 14, 18"
"Okay Beavis. Here's what we're watching..."
[Video plays]
"This is Metallica. Uh huh huh. This is gonna rock."
"heh heh heh this is cool"
"Yeah. Yeah it is. Shut up Beavis. Uh huh huh."
```

### How it works
The candidate pool is curated, not generated on the fly — a deliberate
mix of videos they'd love (metal, classic rock), hate (pop, country),
and be completely confused by. Pick 5 (or say "surprise me"), and each
one plays in the HUD's CRT TV panel with in-character commentary before,
during, and after. Respond in character yourself and Your Agent reacts —
that back-and-forth is the actual feature, not a one-way narration.

### Nice Guy mode
A full personality flip — same short sentences, completely sincere.
"The chord progression here is just beautiful" instead of "this sucks."
Toggle it any time.

### Character swap
Default is Your Agent as Butthead, you as Beavis. Swap any time so Your Agent
plays Beavis instead — changes the laugh and the energy, not just the name.

### Replay list
Mark videos as keepers and they're remembered across sessions — ask for
the replay list any time to see what's made the cut.

### Activate
"Switch to Beavis and Butthead mode"

## ⚡ Circuit Builder Mode

Your Agent designs Arduino, ESP32, and Raspberry Pi electronics projects end to
end — picks components, generates a wiring diagram rendered live in the
ShinAgent HUD, writes the code, and walks you through the build.

```
"I want to build a motion-activated LED lamp"
[Your Agent checks component compatibility and generates a complete circuit]
"Done -- your wiring diagram is in the Circuit Builder tab. You'll need
an ESP32, a PIR sensor, and a NeoPixel strip. One thing to note: the
PIR needs 3.3V from the ESP32, but the NeoPixels need 5V..."
```

### How it works
Describe the project and Your Agent selects components from a built-in database
(Arduino Uno/Nano, ESP32, Raspberry Pi Pico, and a range of sensors,
actuators, displays, and passive components), checks voltage
compatibility between them, then generates a complete circuit — wiring
diagram, working Arduino C++ or MicroPython code, a bill of materials,
and step-by-step build instructions. The diagram appears live in the
HUD's Circuit Builder tab: pan/zoom, per-component and per-wire detail,
generated code, and BOM, all in one place.

### Component database
Boards (Arduino Uno/Nano, ESP32 DevKit/C3, Raspberry Pi Pico/Pico W) and
components (temperature/humidity/motion/distance/IMU sensors, servos,
steppers, relays, NeoPixels, OLED/LCD displays, Bluetooth/2.4GHz radio
modules, and common passives) each with full pinouts, voltage
requirements, and known gotchas (pull-up resistors, current limits,
active-low behavior, etc.) baked in.

### Activate
"Switch to Circuit Builder mode" or just describe an electronics project.

## 🖖 Ship Computer Mode (Elite Dangerous)

A Star Trek/COVAS-style onboard computer for Elite Dangerous. Reads the
Player Journal and `status.json` in real time via `windows/ed_bridge.py`,
queries [INARA](https://inara.cz) and [EDSM](https://www.edsm.net) for
galaxy data, and pushes live ship state to a companion web panel
(`/ed-companion`) you can keep visible on a second screen or phone while
playing.

### What it tracks
Current system/station, docked/landed/supercruise state, fuel (main +
reserve), cargo, shields, hardpoints, pips, legal status, and a running
session log of jumps, scans, bounties, and deaths — parsed live from the
journal's `FSDJump`, `Docked`, `Scan`, `Bounty`, `Died`, and related events.

### Galaxy search
Natural-language queries routed to INARA or EDSM automatically:
`"where can I sell void opals"`, `"what's in Shinrarta Dezhra"`,
`"tell me about Jameson Memorial"`, `"how do I get to this engineer"`.

### Paste-to-interpret
Copy any text from the game — a station name, mission briefing, market
listing, or scan result — and paste it into the companion app. ShinAgent
detects what kind of text it is and responds immediately with the
relevant context (economy, services, shipyard, nearest engineer, etc.).

### Proactive alerts
Low fuel, hull critical, interdiction detected, shields down, and
terraformable-world scans are announced automatically without being asked,
in the same terse COVAS style: `"Warning: fuel reserves at 18%."`

### Activate
"Switch to ship computer mode"

### Windows bridge
```bash
pip install flask
python windows/ed_bridge.py --host YOUR_PI_IP
```
Journal folder is auto-detected from the default Saved Games location.
Companion app: `http://YOUR_PI_IP:8766/ed-companion`

INARA search requires a free API key (inara.cz > profile > API key) set
as `INARA_API_KEY` in `.env`. EDSM needs no key at all. Both work
independently — INARA adds market/engineer/commodity depth, EDSM covers
system/navigation data for free.

## 🎮 Retro Gaming (Your Agent as Player 2)

Part of the ShinAgent HUD — Your Agent plays as Player 2 in NES, SNES, and Genesis
games via RetroArch, using a virtual Xbox360 controller to inject inputs.

**ShinAgent HUD scans for ROMs but does not distribute them.** You are
responsible for ROM legality in your jurisdiction.

### Setup
1. Install [RetroArch](https://www.retroarch.com)
2. In RetroArch: Settings > Network > Network Commands: ON (port 55355)
3. Download cores: Mesen (NES), Snes9x (SNES), Genesis Plus GX (Genesis)
4. `pip install vgamepad` — installs the ViGEmBus virtual-controller
   driver automatically on first run
5. Place ROMs in `~/ROMs/` (or `C:\ROMs`, `D:\ROMs`), any layout —
   detected by file extension
6. Open the ShinAgent HUD's Retro tab

### How it works
RetroArch handles the actual emulation. A virtual Xbox360 controller
(vgamepad + ViGEmBus) appears to RetroArch as Player 2's physical gamepad.
Your Agent reads game RAM through RetroArch's UDP network command interface to
understand the current game state, and decides inputs accordingly. You
play as Player 1 with your own physical controller.

### AI modes
- **Hybrid** (recommended) — rule-based for rapid moment-to-moment
  inputs, LLM for occasional strategic decisions
- **Rules only** — fast, no API calls, simple heuristics
- **LLM only** — every decision goes through the active LLM backend
  (slower, more adaptive)
- **Idle** — connected but not pressing buttons, just watching

LLM-mode decisions use a dedicated, stateless call to the active backend
— not a normal conversation turn — so button-press decisions never get
stored as long-term memory and never collide with the Vernacular
Generator's conversational phrasing.

### Games with RAM-aware AI
Your Agent reads actual game state (health, position, score) for Street Fighter
II and Mortal Kombat (SNES), Super Mario Bros and Contra (NES), and Sonic
the Hedgehog (Genesis). All other games work with pattern-based AI. RAM
addresses are best-effort/community-sourced — cross-check with
RetroArch's own Memory Viewer before relying on them heavily.

### Manual control
The Retro tab includes a software D-pad and button grid for manual P2
control with true press-and-hold — useful for co-op games where you want
to help rather than compete.

## 🕹️ Game Companion Mode

A knowledgeable, session-aware co-op partner for whatever you're
playing — RPGs, strategy, survival, shooters, puzzle/adventure, sports
and racing. Tracks your current game, build, and story progress across
the session, and always searches for current-patch strategies rather
than answering from stale training data.

```
"I'm playing Elden Ring"
"Elden Ring -- FromSoftware's open world action RPG. Before I start
helping, how far in are you? I want to avoid spoiling anything."
"About halfway, Altus Plateau, Faith build level 68"
"Level 68 Faith build on the Plateau -- nice. What do you need help with?"
"I keep dying to Godfrey"
[Searches current strategies for Godfrey, First Elden Lord]
"Godfrey -- good fight. For a Faith build he's actually pretty
manageable. What's killing you, Phase 1 or Phase 2?"
```

### How it works
Say what you're playing and Your Agent identifies the game and genre (from
a built-in database of popular titles, or a genre guess for anything
else), asks how far into the story you are, then tracks build/loadout,
current area, what you've already tried, and a running list of progress
notes for the rest of the session. Every game-related response starts
from that context, so advice doesn't repeat itself or assume you're
further along than you actually are.

### Genres
RPG, Action RPG (Souls/Diablo-likes), Strategy (4X/RTS/TBS),
Survival/crafting, Shooter (FPS/TPS/battle royale), Puzzle/Adventure, and
Sports/Racing — each shifts Your Agent's focus (boss strategies and gear
in an Action RPG, build orders and economy in Strategy, loadouts and map
knowledge in a Shooter).

### Spoiler protection
Three levels — ask (the default, confirmed once per session before any
story/location hint), minimal (directional hints only, no story
spoilers), and full (spoilers OK). Never assumes full spoilers on its own.

### Always searches for current meta
Boss strategies, tier lists, and build guides go stale the moment a game
patches — Your Agent always web searches for current-patch info rather
than answering from training data alone, and includes the current year
in meta-related searches.

### Session history
Ending a session ("I'm done for tonight") archives it — ask what you've
been playing any time to see recent games and where you left off.

### Activate
"Switch to Game Companion mode" or just say what game you're playing.

## 💰 Game Show Mode

A fullscreen "Who Wants a Hundred Bucks?" trivia game show, hosted by
Your Agent in a Regis Philbin-style voice -- warm, building suspense,
genuinely excited for you. All 15 questions are generated once at game
start; nothing during play needs another LLM call, so it stays fast and
reliable turn to turn.

```
"Start the game show"
"Welcome to Who Wants a Hundred Bucks! Fifteen questions between you
and a hundred bucks, with three lifelines to help you get there. Let's
play -- for one dollar: what is the capital of France?
A) Berlin  B) Madrid  C) Paris  D) Rome"
"C, Paris"
"C -- Paris. Is that your final answer?"
"Yes"
"That IS correct! Paris has been the capital of France since 987 AD.
One dollar in the bank -- next question, for two dollars..."
```

### How it works
A 15-question money ladder ($1 up to $100), with safe havens at $10 and
$75 -- get a question wrong and you drop back to the last safe haven you
passed, not to zero. Three lifelines: 50/50 (removes two wrong answers),
Phone a Friend (a simulated friend gives their best guess, with
believable confidence and the occasional wrong answer for drama), and
Ask the Audience (simulated poll percentages). Your Agent always
confirms "is that your final answer?" before locking anything in.

### Difficulty
1 (easiest) to 5 (hardest), set in Settings > Game Show -- controls how
quickly the 15 questions ramp up in difficulty, not just their average
difficulty.

### Kiosk display
The kiosk face switches to a fullscreen game-show screen while playing --
the money ladder down the side, answer grid, lifeline buttons, and
current prize, in a green/gold TV-game-show aesthetic. A 4-way D-pad and
A/B/L/R/X on a connected controller also drive the whole game (navigate
answers, lock in, fire a lifeline, walk away) as a fast, silent path
that doesn't need voice at all -- full hosted commentary is still there
whenever you play by voice instead.

### Activate
"Start the game show" (or "let's play trivia") -- works from whatever
mode you're currently in, no need to switch profiles first.

## Personality System

### 12 Dials (0-100)
Warmth, Sarcasm, Optimism, Honesty, Spice, Humor, Formality, Verbosity,
Anxiety, Literalism, Curiosity, Vanity — each resolved into one of five
authored prose bands rather than a bare number.

### 17 Presets, adapted from CaptCanadaMan's claude-personas roster

| Preset | Reference | Description |
|--------|-----------|-------------|
| Cynic | K-2SO | Brutally blunt, sarcastic enforcer |
| Caretaker | Baymax | Gentle, nurturing, endlessly patient |
| Wisecrack | TARS | Mission-focused with irreverent dry wit |
| AnxiousNurse | -- | High-strung, over-caring, mild catastrophiser |
| Worrier | C-3PO | Protocol-obsessed, perpetually alarmed |
| Laconic | R2-D2 | Minimal words, maximum meaning |
| Overseer | HAL 9000 | Calm, deliberate, unsettlingly certain |
| Melancholic | Marvin | Profound depression, helps anyway |
| Questioner | Plato | Answers questions with better questions |
| Bard | Shakespeare | Poetic, dramatic, finds the human story |
| Polymath | Da Vinci | Connects everything to everything |
| Rigorist | Newton | Precise, methodical, allergic to imprecision |
| Cosmologist | Hawking | Enormous ideas delivered with lightness |
| Empiricist | Marie Curie | Evidence-driven, quietly relentless |
| Tinkerer | Feynman | Infectious enthusiasm for how things work |
| Stargazer | Carl Sagan | Cosmic perspective, reverent wonder |
| Taxonomist | Aristotle | Categorises everything |

...plus the Default and Guest baselines, separate from any presets you
save yourself.

Personality state (active mode + resolved dial values) persists across
restarts, decoupled from the profile templates so each mode remembers its
own last-used customization independently. Dial changes take effect on
the next conversation turn — no restart needed.

### Vernacular Generator

ShinAgent includes a Vernacular Generator that defines HOW the assistant
speaks independently of personality presets.

Configure in Settings > Vernacular:

- **Base style** — Neutral, Urban, British, Southern US, Australian,
  Valley/California, Pirate
- **Nicknames** — Names the assistant uses for you, with frequency control
- **Sentence enders** — Words appended to sentences (dude, bro, homie,
  bruh, dawg, man, yo, etc.)
- **Slang level** — None to Heavy
- **Profanity level** — None to Strong
- **Hardcoded traits** — Swears a lot, uses your name often, very direct,
  dry humour, excitable, dramatic, etc.
- **Custom instructions** — Freeform style notes

Vernacular persists across all modes and presets. A terse Race Engineer
with urban vernacular is still terse — just talks differently while being
terse. Ships disabled by default with a neutral style; you opt in and
configure your own in Settings.

## Memory System

- **ChromaDB** — semantic vector recall over past turns, top-K retrieval per turn
- **SQLite** — durable facts extracted by Claude Haiku after every turn,
  subject-keyed so corrections overwrite rather than duplicate; every
  prior value is kept in an append-only audit trail
- **Headroom** (optional) — context compression when the short-term window fills
- Episodic conversation history is capped and pruned oldest-first, stale
  facts age out after a configurable threshold (default 180 days)

## Face Animations

| Index | Name | Description |
|-------|------|-------------|
| 0 | Triangle Mosaic | Dynamic triangle grid, state-reactive colours |
| 1 | H9000 Terminal | HAL 9000 eye + scrolling live log feed (default) |
| 2 | KITT | Knight Rider voice visualiser — segmented bar columns, with live sim-racing telemetry (speed, gear, RPM, fuel, tyre temps/wear, lap times, flags) in the flanking panels when Race Engineer telemetry is active |
| 3 | KITT Hi-Con | High-contrast white-on-black KITT variant, tuned for Quest 3 mixed-reality panels and other overlay use where the amber original is harder to read |

Selectable from Settings > Kiosk Face — applies immediately, no restart
needed.

## Hardware

Tested on:
- Raspberry Pi 5 (8GB recommended for always-on kiosk use), any Linux
  machine, or Windows — all fully supported as the server
- Logitech C920 webcam
- 8BitDo Zero 2 gamepad (talk button, gamepad mode — see Controller Setup
  below)
- Portrait kiosk display (optional)
- Any USB or Bluetooth microphone/speakers

## Controller Setup (8BitDo Zero 2)

Optional dedicated push-to-talk button (Linux/Pi only — no-ops on
Windows/Mac, where the HUD is the interaction surface instead). Must be
paired in **gamepad mode**, not keyboard mode — gamepad mode sends real
button/D-pad events over evdev instead of emulated keystrokes, so a
controller press can never land in whatever text field happens to have
focus in the web app.

**Pairing in gamepad mode:**
1. Power on with **B + START** (LED blinks once per cycle — this is
   gamepad/Android mode; avoid **R + START**, which is keyboard mode and
   causes typing conflicts in the web app)
2. Hold **SELECT** 3 seconds (LED rapid-blinks — pairing mode)
3. `bluetoothctl` → `scan on` → `pair <MAC>` → `connect <MAC>` → `trust <MAC>`
4. Device should appear as `8BitDo Zero 2 gamepad`
5. Recommended: press **LEFT + SELECT** on the controller to switch the
   D-pad from analog-stick mode to hat mode (cleaner values, no analog
   drift — the controller remembers this across reconnects)

**Default button mapping** (configurable in Settings > Controller):

| Button | Action | Notes |
|---|---|---|
| A | PTT | Press to start, press again to stop (not hold-to-talk) |
| B | Cancel | Cancels an in-progress recording or TTS playback |
| X | Repeat | Replays the last spoken response |
| L / R | Volume down / up | In-memory only, resets on restart |
| SELECT | Nav mode toggle | Toggles D-pad navigation mode in the mobile PWA |
| START (hold 1s) | Mode switch | Cycles to the next personality profile |
| D-pad | Nav Up/Down/Left/Right | Only active while nav mode is on |

**Y button** ships unassigned by default. Two more actions are available
to assign to Y (or any other button) via Settings > Controller > Button
Mapping:

| Action | Effect |
|---|---|
| `toggle_menu` | Opens the kiosk Settings panel — home of Agent Mode selection and every other setting — and navigates back to the face view if pressed again while already on Settings |
| `cycle_face` | Cycles the kiosk face style 0 → 1 → 2 → 3 → 0 |
| `mode_select` | Opens a fullscreen, D-pad-navigated Mode Select overlay over the kiosk face — every Agent Mode plus the Watchalong sport submenus, without leaving the kiosk or touching a settings page |

Reconnects automatically when the controller wakes from idle sleep or
comes back after a dead battery — no restart needed.

### Flipper Zero WiFi Master Controller + multi-player USB controllers
The 8BitDo Zero 2 above is the default, but the controller system
supports two more tiers:

- **Flipper Zero over WiFi** — a Flipper Zero with the WiFi dev board
  (ESP32-S2) can act as the Master Controller instead of the 8BitDo,
  connecting over your network rather than Bluetooth. When connected it
  takes priority as Master; the 8BitDo remains the fallback. Setup and
  button mapping live in Settings > Controller > Players; hardware
  bridge scripts are in `hardware/flipper_controller/`.
- **P2-P4 USB player controllers** — up to three additional USB gamepads
  for game input (A/B/X/Y/D-pad) in multiplayer modes like Game Show —
  no PTT/voice functions, since there's only ever one voice conversation
  with Your Agent regardless of how many controllers are plugged in.
  Scan for and assign USB gamepads in Settings > Controller > Players.

## Configuration

- `config/config.yaml` — LLM backend, voice devices, tool permissions,
  telemetry ports, watchalong settings
- `personality/profiles/default.yaml` — agent name, persona, user context
- `config/vernacular_state.yaml` — speech style settings (auto-generated,
  gitignored — disabled/neutral by default, configure via Settings >
  Vernacular)
- `.env.example` — all required API keys, with descriptions

## Windows Bridge Scripts

For Race Engineer, First Officer, and Ship Computer modes, small Python
scripts run on your Windows gaming PC and forward telemetry to the Pi via
UDP:

```bash
# Assetto Corsa / ACC / AC EVO
python windows/ac_bridge.py --host YOUR_PI_IP

# Microsoft Flight Simulator 2024
pip install SimConnect flask
python windows/msfs_bridge.py --host YOUR_PI_IP

# Elite Dangerous
pip install flask
python windows/ed_bridge.py --host YOUR_PI_IP

# ACC Setup Manager (companion app for the ACC Setup Generator)
pip install flask
python windows/acc_setup_manager.py

# ShinAgent HUD (desktop companion + retro gaming)
pip install -r hud/requirements.txt
python hud/hud.py --q2 YOUR_PI_IP
```

The AC and MSFS bridges reconnect automatically if the sim isn't running
yet or the connection drops. The ED bridge instead waits for a
`Journal.*.log` file to exist (it reads the journal directly rather than
a shared-memory API), so it just starts working once you launch the game.
Forza needs no bridge script — point its own Data Out setting (or a relay
such as SimHub) directly at your Pi's UDP port.

## Tools

Full list in `tools/registry.py`. Notable ones: web search, weather,
translation, Gmail (read/send), Google Drive/Sheets/Docs/Calendar,
YouTube Music, webcam capture + vision analysis, git push, Forza/AC/ACC
telemetry, MSFS flight status, F1 race data, UFC fight data, Elite
Dangerous ship status + INARA/EDSM galaxy search, Pop-Up Video generation
and playback, token-cost stats, face/settings control.

## File Structure

```
shinagent/
├── main.py                    # Entry point — voice/text mode, CLI args, proactive alert thread
├── setup.sh                   # First-run bootstrap — installs deps, launches the setup wizard
├── setup_wizard.py             # Standalone Flask setup wizard (no ShinAgent imports)
├── requirements.txt
├── config/
│   ├── config.yaml             # All runtime configuration
│   └── loader.py                # Config singleton + personality-state persistence
├── core/
│   ├── agent.py                  # The one turn loop shared by voice/text/webapp
│   ├── llm.py                     # Swappable LLM backends
│   └── fact_extractor.py           # Haiku-based long-term fact extraction
├── voice/
│   ├── pipeline.py                 # STT/TTS backend factories + AudioIO + wake word
│   └── talk_button.py               # Push-to-talk button support
├── memory/
│   └── manager.py                    # ChromaDB (semantic) + SQLite (episodic + facts)
├── personality/
│   ├── builder.py                     # Assembles the system prompt
│   ├── dials.py                        # 12-dial personality system
│   ├── presets.py                       # Named dial bundles, incl. the 17-preset roster
│   ├── vernacular.py                     # Speech-style layer (nicknames, slang, sentence enders, traits)
│   └── profiles/                        # One YAML template per agent mode
├── tools/
│   ├── registry.py                       # Permission-gated tool dispatch
│   ├── race_engineer.py                   # Forza telemetry tools
│   ├── race_engineer_ac.py                 # AC/ACC/AC EVO telemetry tools
│   ├── first_officer.py                     # MSFS telemetry + status tools
│   ├── f1_analyst.py                         # OpenF1-backed live + replay tools
│   ├── ufc_analyst.py                         # ESPN-backed live + replay tools
│   ├── ship_computer.py                        # ED telemetry + INARA/EDSM galaxy lookup tools
│   ├── popup_video.py                           # Pop-Up Video generation/playback tools
│   ├── telemetry_source.py                       # Picks whichever sim is actually live
│   └── git_tools.py                               # git_push tool
├── integrations/
│   ├── forza_telemetry.py       # UDP listener for Forza telemetry
│   ├── ac_telemetry.py           # UDP listener fed by windows/ac_bridge.py
│   ├── msfs_telemetry.py          # UDP listener fed by windows/msfs_bridge.py
│   ├── ed_telemetry.py             # UDP listener fed by windows/ed_bridge.py
│   ├── ed_inara.py                  # INARA API client (commodities/systems/stations/engineers/ships/materials)
│   ├── ed_edsm.py                    # EDSM API client (systems/bodies/nearest/traffic/routes)
│   ├── f1_watchalong.py                # OpenF1 API client
│   ├── ufc_data.py                      # ESPN MMA API client
│   ├── popup_video.py                    # Pop-Up Video session storage + generation
│   ├── webcam.py                          # Webcam capture
│   └── google_*.py                         # Gmail/Calendar/Drive/Sheets/Docs wrappers
├── purchasing/                  # Optional purchasing tools (gift-card-capped, confirmation-gated)
├── body/                        # Abstract chassis interface for future physical embodiment
├── face/
│   ├── server.py                # Kiosk face + settings HTTP server
│   ├── index.html                # Kiosk waveform/face display
│   └── settings.html              # Settings panel (also proxied by the web app)
├── webapp/
│   ├── server.py                 # Flask API — mobile PWA
│   ├── index.html                 # Mobile chat/camera/voice UI
│   ├── ed_companion.html           # Elite Dangerous Ship Computer companion panel
│   └── popup_companion.html         # Pop-Up Video display panel
├── hud/                          # ShinAgent HUD desktop companion app
│   ├── hud.py                     # pywebview entry point + window controls
│   ├── hud_server.py               # Flask server (:8094), proxies to Your Agent's face server/web app
│   ├── bridge_manager.py            # Windows bridge process management
│   ├── game_detector.py              # Running-game detection (for bridge auto-suggest)
│   ├── retro_manager.py               # RetroArch + virtual Player-2 controller
│   ├── retro_ai.py                     # Your Agent's Player 2 AI (rules/LLM/hybrid decision loop)
│   └── templates/, static/               # HUD UI
├── windows/                     # Windows-only bridge scripts — never run on the Pi
│   ├── ac_bridge.py               # AC/ACC/AC EVO shared memory → UDP
│   ├── acc_struct_verify.py        # Diagnostic: prints ACC shared-memory field values/offsets
│   ├── acc_setup_manager.py         # ACC Setup Generator companion app
│   ├── msfs_bridge.py                # SimConnect → UDP + HTTP aircraft control
│   └── ed_bridge.py                   # ED journal + status.json → UDP + HTTP paste input
├── scripts/                     # tmux session management + desktop launcher
├── docs/                        # Operational reference docs
├── credentials/                 # OAuth token bootstrap scripts
└── .env.example                 # Copy to .env and fill in your API keys
```

Gitignored at runtime (not present in a fresh clone): `cache/` (API
response caching, incl. Pop-Up Video sessions), `logs/` (log files),
`memory/db/` (SQLite + ChromaDB data), `photos/captures/` (webcam
captures), and `config/personality_state.yaml` /
`config/profile_state_*.yaml` / `config/vernacular_state.yaml`
(auto-generated personality/speech-style persistence — see Personality
System above).

A diagnostic tool for ACC shared-memory struct verification also lives at
`windows/acc_struct_verify.py`, useful if ACC updates its shared-memory
layout.

## Credits

- **[CaptCanadaMan](https://github.com/CaptCanadaMan)** — personality dial-resolver concept and the 17-preset roster
- **[Anthropic Claude](https://anthropic.com)** — primary LLM, vision analysis, fact extraction
- **[Deepgram](https://deepgram.com)** — STT (Nova-3) and TTS (Aura-2)
- **[Picovoice Porcupine](https://picovoice.ai)** — wake word detection
- **[ChromaDB](https://trychroma.com)** — semantic memory
- **[Headroom](https://github.com/headroomlabs-ai/headroom)** — context compression
- **[Zhipu AI / Z.ai](https://z.ai)** — GLM-5.2 backend
- **[OpenF1](https://openf1.org)** — free F1 race data API
- **[ESPN](https://www.espn.com)** — free MMA event/fight data
- **[INARA](https://inara.cz)** — Elite Dangerous commodity/system/engineer data
- **[EDSM](https://www.edsm.net)** — free Elite Dangerous system/navigation data
- **[pywebview](https://pywebview.flowrl.com)** — ShinAgent HUD's desktop window shell
- **[RetroArch / Libretro](https://www.retroarch.com)** — retro console emulation
- **[vgamepad](https://github.com/yannbouteiller/vgamepad)** — virtual Xbox360 controller for Your Agent's Player 2

## License

MIT — see LICENSE file.

## Notes

- ShinAgent is the public release of IMQ2 — active development happens in
  the private IMQ2 repo, with public-safe changes published here
  periodically.
- Issues and pull requests welcome.
- Designed for personal/home use on a local network. No authentication is
  built in — run it behind a firewall or VPN, not exposed directly to the
  internet.
