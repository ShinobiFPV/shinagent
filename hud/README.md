# ShinAgent HUD

Desktop companion app for Q2. Provides a data dashboard,
bridge management, and module-specific displays.

## Requirements

    pip install -r hud/requirements.txt

## Run

    # From the imq2 project root:
    python hud/hud.py

    # Options:
    python hud/hud.py --q2 192.168.1.100    # Q2 Pi IP
    python hud/hud.py --borderless           # start frameless
    python hud/hud.py --ontop                # always on top
    python hud/hud.py --width 1024 --height 700

## Demo Mode

Run the HUD with spoofed telemetry data for layout inspection and
development -- no Q2, game, or bridge connections needed.

    python hud/hud.py --demo
    python hud/hud.py --demo --module race_engineer
    python hud/hud.py --demo --module freeroam
    python hud/hud.py --demo --module first_officer
    python hud/hud.py --demo --module ship_computer
    python hud/hud.py --demo --module f1_watchalong
    python hud/hud.py --demo --module ufc_watchalong
    python hud/hud.py --demo --module popup_video
    python hud/hud.py --demo --module retro

### Available modules

| Module | What's spoofed |
|--------|---------------|
| race_engineer | Live race telemetry, tyre data, lap times, position |
| freeroam | Open world driving, drift detection, jump detection, landmark location |
| first_officer | MSFS flight data, altitude, heading, autopilot |
| ship_computer | ED ship status, events, fuel |
| f1_watchalong | F1 session status |
| ufc_watchalong | UFC event status |
| popup_video | Film pop-up bubbles |
| retro | RetroArch game running, P2 AI active |

A module can also be switched live from within the HUD -- the amber demo
banner at the top has a "Switch module" link that swaps the active demo
data set (via `/api/demo/switch`) without restarting the process.

### What works in demo mode

- All data panels update with animated fake values
- Module indicator animates correctly
- ACC setup cards show sample setups
- Bridge status shows mixed running/stopped state
- ED event log shows sample events
- Pop-Up Video shows a sample bubble
- Tab navigation, toolbar collapse, and borderless mode all work normally

### What doesn't work in demo mode

- Write operations are acknowledged but not executed (apply setup, start/stop
  bridge, launch game, retro AI start/stop all return `ok: true` but nothing
  actually happens)
- Q2 chat returns a demo acknowledgement instead of a real reply
- The demo banner's module switcher lets you switch between data sets
  without restarting the process

## Tabs

- **Status**     -- Q2 connection, active module, detected games, F1/UFC
  Watchalong live status
- **Telemetry**  -- Live race data (AC preferred, falls back to Forza) plus
  a First Officer flight-data panel (altitude/airspeed/heading/autopilot/
  fuel) shown only while that profile is active
- **ACC Setups** -- Browse, apply, delete, and generate ACC car setups
- **Ship Computer** -- Elite Dangerous status, paste input, galaxy search,
  INARA/EDSM quick links
- **Pop-Up Video**  -- Film pop-up display panel with auto-advance
- **Retro**      -- Q2 plays Player 2 in NES/SNES/Genesis games via
  RetroArch (Windows only)
- **Bridges**    -- Start/stop Windows bridge scripts (Windows only)
- **Web**        -- Quick links to INARA, EDSM, OpenF1, UFC Stats

## Borderless overlay mode

Click the borderless button in the toolbar to toggle a semi-transparent
background (CSS only — see note below on the native frame). Drop it over
dead space on your monitor, or use always-on-top to overlay on a game.

Hide the toolbar: click the collapse icon to shrink it to a 4px strip.
Click the strip to expand it again.

**Native frame vs. CSS transparency**: pywebview can't reliably toggle a
window's actual OS-level frame after creation across its backends (Edge
WebView2 on Windows, GTK/Qt elsewhere). The real frameless window comes
from `--borderless` at launch; the in-app toggle button only changes CSS
background opacity, which still gives a usable "looks borderless" effect
without a restart.

## Windows only features

Bridge management (auto-launching ac_bridge.py, msfs_bridge.py,
ed_bridge.py) is Windows only. Pi/Linux users see an informational
message in the Bridges tab instead of controls. Retro gaming (below) is
Windows only for the same reason -- ViGEmBus is a local Windows driver.

## Retro Gaming (Q2 as Player 2)

ShinAgent HUD includes a retro gaming module where Q2 plays as Player 2 in
NES, SNES, and Genesis games via RetroArch.

**ShinAgent HUD scans for ROMs but does not distribute them.** You are
responsible for ROM legality in your jurisdiction (this disclaimer is also
shown directly in the Retro tab).

### Setup

1. Install RetroArch from retroarch.com
2. In RetroArch: Settings > Network > Network Commands: ON (port 55355)
3. Download cores: NES (Mesen), SNES (Snes9x), Genesis (Genesis Plus GX)
4. Install vgamepad: `pip install vgamepad` (ViGEmBus driver installs
   automatically on first run)
5. Place ROMs in `~/ROMs/` (or `C:\ROMs`, `D:\ROMs`), organized by system
   or mixed -- detected by file extension either way

### How it works

- RetroArch handles the actual emulation
- A virtual Xbox360 controller (via vgamepad + ViGEmBus) appears to
  RetroArch as Player 2's physical gamepad
- Q2 reads game RAM through RetroArch's UDP network command interface
  (`READ_CORE_RAM`) to understand the current game state
- You play as Player 1 with your physical controller

### AI decision architecture

"LLM" and "hybrid" modes deliberately do **not** route through Q2's normal
conversational agent. A dedicated, stateless endpoint
(`webapp/server.py`'s `POST /retro/decide` → `tools/retro_decide.py`)
makes a one-shot call straight to the active LLM backend with a small,
purpose-built prompt, bypassing memory storage, the tool-use loop, and
personality/Vernacular Generator injection entirely. Routing "press
RIGHT+B" decisions through the full agent would (a) store every game frame
as a permanent episodic memory turn, polluting real conversation history,
and (b) actively fight a clean JSON-array response, since the Vernacular
Generator's whole job is pushing responses toward conversational,
voice-flavoured prose.

### AI modes

- **Hybrid** (recommended): rule-based for rapid moment-to-moment inputs,
  with roughly 15% of decisions handed to the LLM for strategy
- **Rules only**: fast, no API calls, simple heuristics
- **LLM only**: every decision goes through `/retro/decide` (slower,
  network round-trip per decision, more adaptive)
- **Idle**: Q2 is connected but not pressing buttons -- just watching

Decision cadence is 400ms by default (`RetroAIController._frame_ms`) --
fast enough to react in most games, slow enough not to hammer the LLM API
in LLM/hybrid mode. Fighting games may benefit from a shorter interval;
turn-based games can go much longer. Adjust per game by changing
`_frame_ms` in `hud/retro_ai.py`.

### Supported games (RAM-aware)

Games with a known RAM map in `hud/retro_ai.py`'s `GAME_RAM_MAPS` get
smarter AI, since Q2 can read actual game state (health, position, score)
instead of just guessing blind:

- Street Fighter II (SNES) -- health and position
- Mortal Kombat (SNES) -- health
- Super Mario Bros (NES) -- position, lives, coins
- Sonic the Hedgehog (Genesis) -- rings, lives, position
- Contra (NES) -- lives and position

These addresses are best-effort / community-sourced, not independently
verified against a live capture in this repo -- cross-check with
RetroArch's own Tools > Memory Viewer before relying on them heavily. All
other games work with pattern-based AI (no RAM reading).

### Manual P2 control

The Retro tab includes a software D-pad and button grid for manual P2
control -- useful for co-op games where you want to help rather than
compete. The D-pad uses true press-and-hold (mousedown/mouseup map to
`VirtualP2Controller.hold()`/`release()`), not a fixed-duration tap, since
held-vs-tapped genuinely changes jump height/run distance in platformers.

## Module indicators

The small animated icon in the toolbar changes based on Q2's
active profile:

- Default:         Breathing green dot
- Race Engineer:   Rev counter arc (red)
- First Officer:   Attitude indicator ball (blue/brown)
- Ship Computer:   Radar sweep (orange)
- Watchalong:      Pulsing ring
- Pop-Up Video:    Film sprocket tick

## Architecture notes

- The HUD server runs on port 8094 -- doesn't conflict with Q2's own
  face server (8765) or webapp (8766).
- All Q2 communication goes through the HUD server as a proxy (avoids
  CORS issues in pywebview and keeps API keys/hostnames off the page).
  Racing telemetry (speed/gear/rpm/fuel/tyres/lap times) comes from
  face/server.py's `/state`, which already normalizes AC and Forza into
  one shape (AC preferred when both are live) — there's no separate
  per-source telemetry endpoint on the Q2 side, so `/api/telemetry/forza`
  and `/api/telemetry/ac` just filter that same combined object. MSFS
  flight data doesn't fit that shape at all, so it has its own dedicated
  route (`webapp/server.py`'s `/msfs/state`, added alongside this HUD,
  mirroring the existing `/ed/state` pattern).
- F1/UFC Watchalong status comes from face/server.py's `/settings`
  response (not `/state`) since both involve a live network call to
  OpenF1/ESPN — kept off the fast polling path on the Q2 side already, and
  polled here on its own slower 15s interval to match.
- pywebview 5.x required for frameless + transparent support.
- The module indicator canvas animates at 30fps independently of the
  state poll (1.5s) -- smooth animation, efficient polling.
- Bridge manager uses `subprocess.CREATE_NO_WINDOW` on Windows so bridges
  run silently in the background.
- The Web tab opens URLs in the system default browser via
  `webbrowser.open()` -- INARA, EDSM etc open in the OS browser, not
  inside the HUD window (cleaner for complex web apps).
- Each `static/modules/*.js` file adds real, incremental behaviour on top
  of the shared logic in `hud.js` rather than duplicating it: setup
  deletion (ACC), galaxy search (Ship Computer), flight-data readout
  (First Officer), pit status (Race Engineer), F1/UFC live status
  (Watchalong), the pop-up auto-advance timer (Pop-Up Video), and the
  entire Retro tab (`retro.js` -- game library, launch, AI control, manual
  P2 D-pad; only a one-line dispatch hook lives in `hud.js` itself, same
  pattern as every other tab).
- `hud/retro_manager.py` and `hud/retro_ai.py` run in-process inside
  `hud_server.py` (not proxied like the Q2-bound routes) since RetroArch
  and vgamepad/ViGEmBus are both local to whichever machine the HUD runs
  on -- only the LLM decision call itself (`/api/retro/decide`) actually
  leaves this process, proxying to Q2's webapp.
