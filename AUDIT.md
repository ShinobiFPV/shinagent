# Q2/IMQ2 Codebase Quality Audit

Date: 2026-07-06
Scope: full repo — `core/`, `face/`, `integrations/`, `tools/`, `voice/`,
`webapp/`, `windows/`, `config/`, `memory/`, `personality/`, `main.py`.

Method: four parallel research passes (core/memory/voice; integrations/Windows
bridges; tools/personality alignment; face+webapp settings round-trip) plus
direct verification of the specific checklist items in the audit request
(LLM backend registry, tool registry completeness, `AGENT_MODES`, webapp
routes, `config.yaml` sections). All Critical and High findings below were
fixed directly in this pass and verified — either by `py_compile`, by a
targeted functional test, or both. Medium and Low findings are documented
here rather than fixed, per the audit's own triage instructions.

There is also a pre-existing `settings_audit.md` in the repo root from an
earlier, narrower audit of just the settings system (persistence bugs,
profile-switcher, mic device list). Its fixes are already in the codebase;
this audit re-verified its claims still hold and did not duplicate them.

## Executive summary

The codebase is larger and more feature-rich than its test coverage (there
is none — no pytest, no CI). That shows up as a specific *pattern* of bug,
repeated across several features added this session and last: **a tool
function gets written and works in isolation, but the registration step
that wires it into `tools/registry.py` gets missed**, so the personality
profile's `tool_instructions:` prose confidently tells the LLM to call
something that doesn't exist. Two of this audit's five Critical findings
are exactly that (`get_replay_status`, `set_popup_title`) — both fixed by
registering the missing `BaseTool` subclasses that were already sitting
there, fully implemented, just never wired in.

The second recurring pattern is **background threads with narrower
exception handling than the rest of the codebase's own stated convention**
("never let a subsystem crash the main chat turn"). `RaceEngineerAlertThread`
gets this right; `WakeWordDetector`, the talk-button listener, and the
webcam capture loop didn't, so any of the three could go permanently silent
for the rest of a session after one bad read. All three are fixed.

Nothing found here indicates a design problem — the architecture (single
`IMQ2Agent`, swappable LLM backends, tool registry with a uniform
permission gate, per-mode personality profiles) holds up well under this
level of scrutiny. The issues are almost all "a step got skipped," not
"the approach is wrong."

## Summary table

| Category | Critical | High | Medium | Low |
|---|---|---|---|---|
| Crash risks | 3 | 4 | 1 | 0 |
| Integration completeness | 1 | 3 | 3 | 0 |
| Dead code | 0 | 0 | 5 | 0 |
| Inconsistencies | 2 | 4 | 3 | 1 |
| Performance | 0 | 0 | 3 | 0 |
| Security / hygiene | 0 | 1 | 1 | 1 |
| Windows bridge completeness | 0 | 1 | 1 | 0 |
| Profile/tool alignment | 2 | 1 | 0 | 0 |
| Settings round-trip | 0 | 2 | 3 | 0 |
| API reliability | 0 | 0 | 2 | 0 |

(Several findings span more than one category; each is counted once, under
its primary category, to avoid double-counting in the table.)

## Issues fixed during this audit

### Critical

**AUDIT-001** `integrations/forza_telemetry.py` — FH5 packet parsing was
silently broken for everyone running Forza Horizon 5 (not 6). The FH5
struct format was derived via `_FH6_FORMAT.replace("Iff", "", 1)`, which
strips the *first* occurrence of that substring — landing at
`TimestampMS`+`EngineMaxRpm`, not the intended `CarGroup`+
`SmashableVelDiff`+`SmashableMass` block. Every genuine 311-byte FH5 packet
failed to match either `_FH5_SIZE` or `_FH6_SIZE` and was silently dropped,
forever, with zero log trail. **Fix:** replaced the derived format with its
own explicit literal (311 bytes, verified via `struct.calcsize` and a
round-trip pack/unpack test with real field values landing at the correct
offsets). FH6 parsing re-verified unaffected.

**AUDIT-002** `personality/profiles/watchalong_replay.yaml` told the LLM to
call `get_replay_status` for tyre/strategy questions during F1 replay. The
function existed in `tools/f1_analyst.py` but was never registered as a
tool — every call failed with `Unknown tool: get_replay_status`. **Fix:**
added `GetReplayStatusTool` to `tools/registry.py` and its config grant.

**AUDIT-003** `personality/profiles/popup_video.yaml` told the LLM to use
`set_popup_title` to resume a saved session. Same shape of bug — the
function existed in `tools/popup_video.py`, never registered. **Fix:**
added `SetPopupTitleTool`, and also `ClearPopupSessionTool` (same situation:
implemented, unregistered, silently unreachable) to `tools/registry.py`
plus config grants.

**AUDIT-004** `main.py`'s `run_voice_mode()` had no per-turn exception
handling around STT/chat/TTS/playback — any transient error (network
hiccup, SDK exception) crashed the entire voice process, not just the
current turn, directly against the codebase's own stated convention.
**Fix:** wrapped the turn body in `try/except Exception`, logs and returns
to listening rather than exiting.

**AUDIT-005** `memory/manager.py`'s SQLite calls (`store`, `get_facts`,
`prune_episodic`, `episodic_count`) had no exception handling, and are
called unconditionally on every turn from `core/agent.py`. Since
`webapp/server.py` runs as a **separate OS process** with its own
`MemoryManager`/SQLite connection to the same file, concurrent writes can
raise `sqlite3.OperationalError: database is locked` — previously
uncaught, crashing `agent.chat()`. **Fix:** added `PRAGMA busy_timeout =
5000` to reduce contention, and wrapped each call site to degrade
gracefully (`get_facts` returns `[]`, `store`/`prune_episodic` log and
skip) rather than propagate.

### High

**AUDIT-006** `requirements.txt` omitted `flask`/`flask-cors`, despite
`webapp/server.py` being always-started per `main.py` and hard-requiring
them at import. **Fix:** added both.

**AUDIT-007** `requirements.txt` omitted `google-api-python-client`,
`google-auth`, `google-auth-oauthlib`, `google-auth-httplib2` — used by
every Gmail/Calendar/Docs/Drive/Sheets integration. **Fix:** added all
four. Also added `numpy` (used directly, previously only present
transitively), `opencv-python` (`integrations/webcam.py`), `Pillow`
(`tools/generate_icons.py`), and moved `evdev` in with a platform marker
and explanatory comment (replacing a stale `pynput` entry that
`voice/talk_button.py`'s own docstring says was abandoned for Wayland
compatibility reasons). Also split out `windows/requirements.txt` for the
Windows-only bridge scripts (`flask`, `SimConnect`) that never run on the
Pi.

**AUDIT-008** `main.py` didn't validate Grok/Gemini API keys before
constructing the agent, and `IMQ2Agent(llm_override=args.llm)` wasn't
guarded — selecting an unconfigured backend crashed with a raw traceback
instead of a clean message. **Fix:** extended `check_env()` to check
`XAI_API_KEY`/`GEMINI_API_KEY` for those backends (deliberately *not*
`ZAI_API_KEY` for GLM, since `GLMBackend` intentionally falls back to a
local Ollama endpoint when it's unset), and wrapped the agent construction
in `try/except` with an actionable message.

**AUDIT-009 / 010** `voice/pipeline.py`'s `WakeWordDetector._listen_loop`
and `voice/talk_button.py`'s `_read_loop` both had exception handling
narrower than the rest of the codebase's convention — any exception other
than the one specifically anticipated killed the background thread
permanently and silently (wake word stops working; talk button stops
reconnecting). **Fix:** broadened both to catch `Exception`, log, and
continue/fall through to the existing reconnect logic.

**AUDIT-011** `windows/ac_bridge.py` hardcoded its destination IP/port as
module constants with no override, unlike its siblings `msfs_bridge.py`/
`ed_bridge.py` (both have `--host`/equivalent). Since this repo is actively
migrating to a new Pi address, this script alone would require editing and
redeploying source to follow. **Fix:** added `--host`/`--port` args
matching the other bridges' pattern.

**AUDIT-012** `integrations/webcam.py`'s `_capture_loop` had no exception
handling around `cv2` calls, and `self._running` was never reset if the
loop died — meaning a single driver-level failure (camera unplugged)
killed the thread *and* permanently blocked `start()`'s "already running"
guard from ever restarting it. **Fix:** wrapped the per-iteration body in
`try/except`, and reset `_running = False` in a `finally` so a future
`start()` call can recover. Also fixed a misplaced docstring in `start()`
that was silently a dead expression statement, not a real docstring.

**AUDIT-013** `tools/photo_tools.py`'s `analyze_photo` took a fully
LLM-controlled `path` argument with no containment check — an absolute
path to any existing file (e.g. `.env`) would be read (sent to the vision
model) and then **unconditionally moved** into `photos/processed/`.
`show_photo` had the same unrestricted-path issue (lower severity — display
only) plus unescaped path interpolation into a URL. **Fix:** added
`_resolve_within_photos_dir()`, used by both, which resolves the path and
refuses anything outside `PHOTOS_DIR` (verified against absolute-path and
`../` traversal attempts); also URL-encoded the path in `show_photo`.

**AUDIT-014** `personality/profiles/q2_default.yaml`'s GLM-specific
instructions told the model to switch profiles via
`open_settings(action='switch_profile', profile=...)` — `OpenSettingsTool`
only supports `open`/`close` and has no `profile` parameter at all, so this
silently just re-navigated the kiosk display home instead of switching
anything. **Fix:** corrected the instruction to use `switch_agent_mode`
(the tool every other profile/backend actually uses for this).

**AUDIT-015** `face/server.py`'s `VALID_STYLES = (0, 1, 2)` excluded style
`3` (KITT Hi-Con), which is fully implemented in `face/index.html` and
offered as a real option in the settings dropdown — selecting it silently
remapped to style 1 with no error shown. **Fix:** added `3` to
`VALID_STYLES`.

**AUDIT-016** `face/server.py`'s `AGENT_MODES` list (the settings-panel
dropdown) was missing `profiles/ship_computer.yaml` — a real, working
profile only reachable via the `switch_agent_mode` voice/text tool, not
from the UI. **Fix:** added it.

**AUDIT-017** `main.py`'s webapp subprocess launch (`subprocess.Popen(...)`)
had no exception handling, unlike every telemetry listener start right
below it — a bad interpreter path or missing file would crash the whole
app instead of degrading to voice/text-only. **Fix:** wrapped in
`try/except`, logs a warning and continues without the webapp.

**AUDIT-018** `BodyControlTool` (name `body_control`) is gated the same
way every other tool is — via `tools.body_control.{enabled,permission}` in
`config.yaml` — but only `purchasing.body_control.{enabled,permission}`
existed. The permission toggle at `purchasing.body_control` had **zero
effect** on whether the tool was actually offered to the LLM; it silently
fell back to the hardcoded default (`"none"`), which happened to match the
intended state, masking the bug. **Fix:** added the missing
`tools.body_control` section to `config.yaml` (found via a direct
registered-vs-configured cross-check, not one of the four research
passes).

## Medium issues remaining (not fixed — recommended for next phase)

1. **`memory/manager.py`'s `get_facts()` has no limit and facts are never
   pruned** (by design — only episodic rows are capped). Every turn's
   system prompt includes literally every fact ever stored; for a system
   meant to run indefinitely, this is unbounded token growth with no
   mitigation. *Suggested fix: cap by count or add a `stale_after_days`-style
   filter (the field already exists in `config.yaml`'s `memory:` block but
   nothing reads it for facts — only mentioned for episodic rows).*

2. **No backend in `core/llm.py` sets an explicit request timeout** except
   `OllamaBackend` (`timeout=60`). A hung connection to Claude/OpenAI/Grok/
   GLM/Gemini can block a turn indefinitely with no user-visible feedback.
   *Suggested fix: pass a timeout through the SDK client constructor for
   each backend (Anthropic/OpenAI clients both support this).*

3. **No backend inspects `stop_reason`/`finish_reason` for truncation.** A
   reply cut off by `max_tokens` is stored and spoken as if complete, with
   no log line and no continuation logic.

4. **`RaceEngineerAlertThread.run()` logs tick exceptions at `log.debug`**
   (invisible under the default `INFO` level). A persistent bug in any of
   its six `_check_*` methods would silently no-op the entire proactive
   alerts feature with no operator-visible signal. *Trivial fix — bump to
   `log.warning`.*

5. **Telemetry listeners (Forza/AC/MSFS/ED) have no rebind retry.** If
   `sock.bind()` fails at startup (port already in use, e.g. a stale
   process), `self._running` was already set `True` before the thread
   launched and died — `start()`'s "already running" guard makes the
   listener permanently unavailable for the rest of the session even after
   the port frees up.

6. **Windows bridge Flask control endpoints are unauthenticated on
   `0.0.0.0`** (`msfs_bridge.py`'s `/control`, `ed_bridge.py`'s `/paste`,
   `acc_setup_manager.py`'s `/setups/*`). `/control` can toggle autopilot,
   retract gear, apply ACC setups from anything on the LAN. Reasonable for
   a single-user home network (per this project's whole premise) but worth
   a conscious decision rather than an implicit one before any wider network
   exposure.

7. **`ExecutePurchaseTool`'s merchant detection is a plain substring
   match** (`"amazon.ca" in product_url`) rather than a real host/domain
   check — a URL like `amazon.ca.evil.tld` would match. Mitigated by the
   tool's own two-step confirmation flow, but not a real containment.

8. **`tools/git_tools.py` hardcodes Pi-only paths**
   (`/home/your-pi/imq2`, `/home/your-pi/shinlink-os`) with no
   `config.yaml` override — if `git_push`/related tools are granted and
   exercised on the Windows dev machine during the ongoing migration, they
   silently operate on the wrong (or nonexistent) directory tree.

9. **`git_push` stages `git add -A` and pushes with no confirmation step**,
   unlike the codebase's own documented convention for irreversible actions
   (`ExecutePurchaseTool`'s two-step confirm pattern). A stray secret or
   unintended edit gets committed and pushed on a single voice command.

10. **Dead/orphaned code**, five small items, safe to clean up whenever
    convenient:
    - `tools/get_token_stats.py`'s `get_token_stats()` is never imported —
      `GetTokenStatsTool` in `registry.py` reimplements the same logic inline.
    - `tools/ship_computer.py`'s `get_target_info()` — defined, never
      registered, never mentioned in `ship_computer.yaml`. ED target-lock
      info (bounty/faction/legal status) is currently unreachable.
    - `tools/first_officer.py`'s `set_guard_frequency()` — same shape,
      unregistered and unreferenced.
    - `tools/registry.py`'s `CaptureImageTool` class (distinct from the
      registered `CaptureImageTool2`) — dead duplicate using an old
      `/tmp`-only path and a hardcoded model name regardless of the active
      LLM backend.
    - `webapp/server.py`'s `/popup/api/session` route — no caller found
      anywhere in this repo (companion HTML, tools, or settings page).
      Possibly intended for a future/external client; flagged rather than
      removed since that can't be ruled out from static analysis alone.

11. **Settings round-trip gaps in `face/settings.html`/`face/server.py`**
    (independently reconfirmed, one newly found):
    - `output_device`: if the `pactl set-default-sink` subprocess call
      throws, the failure is silently swallowed but the batched save still
      reports `{"ok": true}` — the UI shows "Saved OK" even though the
      setting neither took effect nor persisted.
    - `llm_model`: accepted and persisted by `.apply()`, but no element in
      `settings.html` ever populates or sends it — a fully one-sided,
      currently-dead field (looks like a partially-built "pick a specific
      model" feature).
    - `voice.talk_button.key`/`device_name`: save and load back correctly,
      but have no live effect and no `restart_required` flag — the UI
      implies the change worked when it silently didn't. This is a
      previously-documented limitation in `settings_audit.md`
      (out-of-scope there too); reconfirmed still present, not a
      regression.
    - "Clear local data" button in `settings.html` calls
      `localStorage.clear()`, but nothing in the file ever calls
      `localStorage.setItem()` — every real setting lives server-side, so
      this button is a no-op despite its confirm dialog implying otherwise.

## Low issues noted

Most of these were fixed as inline code comments while touching the
relevant files for a High-severity fix in the same spot; the rest are
noted here since they didn't warrant their own edit:

- `memory/manager.py`'s migration `ALTER TABLE` uses an f-string — safe
  today (hardcoded literals, not user input) but flagged since it was
  explicitly in scope; now has an inline comment explaining why it's safe.
- `integrations/ed_telemetry.py` writes `_last_packet_time` outside the
  class's own lock, inconsistent with its locking discipline elsewhere;
  harmless under CPython's GIL, now commented.
- `windows/acc_setup_manager.py` and `windows/ed_bridge.py`'s docstrings
  both said `pip install flask requests` when neither script imports
  `requests` — fixed (both now say `pip install flask`).
- `requirements.txt` previously had no comment explaining why `evdev` is
  Linux/Pi-only and safe to skip on a dev machine — now documented inline.

## Architecture notes (working well, worth knowing about)

- The single-`IMQ2Agent`-instance-shared-across-interfaces design is
  clean and was not the source of any finding here. The one real cost of
  that design — two OS processes (voice/text + webapp subprocess) sharing
  one SQLite file with no coordination — is exactly what AUDIT-005 above
  addresses; worth keeping in mind for any future feature that adds a
  third writer.
- The tool registry's permission model (`enabled` + `permission ==
  "granted"`, both required) is simple and was never the site of a bug —
  every mismatch found was a *registration* gap (function exists, never
  wrapped in a `BaseTool`), not a permission-check gap.
- The personality profile system's `dial_preset`/`dial_overrides` /
  `tool_instructions_<backend>` layering held up perfectly under
  cross-reference — every `dial_preset` value and every `dial_overrides`
  key across all 8 profiles matched a real preset/dial name. The only
  drift was in prose (tool names referenced in instructions, covered
  above), never in the structural fields.
- Every external API client audited (OpenF1, ESPN, INARA, EDSM) already
  sets explicit timeouts, has sensibly-sized caching relative to its own
  polling cadence, and degrades to a fallback string rather than raising.
  This is a genuinely strong pattern established early and followed
  consistently — worth using as the template for any new integration
  rather than re-deriving it each time.
- Of the five telemetry integrations, AC and ED are the most complete and
  defensively written (attach-only shared memory with an explicit
  race-condition rationale for AC; per-journal-event handling with
  rotation support for ED). MSFS is close behind. Forza was structurally
  fine but had the FH5 parsing bug fixed in this pass. Webcam is the
  thinnest — it's a genuinely different shape of integration (local
  device, not a network listener) and was the one place lacking the
  broad per-iteration exception guard used everywhere else; now fixed.

## Recommendations for the next phase

1. **A registration-completeness check would have caught two of this
   audit's five Critical bugs automatically.** Something as simple as a
   script that scans every profile YAML's `tool_instructions*:` prose for
   tool-name-shaped tokens and cross-references them against
   `ToolRegistry._register_defaults()` would catch this exact class of bug
   at commit time rather than at "the LLM tries to call it and fails."
   Worth writing even without a full test suite.
2. **A minimal smoke test for `core/agent.py.chat()`** (mock the LLM
   backend, assert a full turn round-trips through memory without
   exceptions) would have caught AUDIT-004/005 much earlier and is cheap
   to maintain going forward.
3. **The `RaceEngineerAlertThread` cooldown/frequency-tier tables are
   becoming a lot of near-duplicated structure** (six near-identical
   dicts for racing/flight/F1/UFC). Not a bug — flagged in the audit
   request's own "dead code" category as something to watch, but nothing
   here rises to "fix now." If a fifth watchalong-style sport gets added,
   this is worth generalizing into one data-driven table before a sixth
   copy gets pasted in.
4. **Given how much of this session's bug count came from
   registration-step gaps**, consider a lightweight startup self-check in
   `ToolRegistry.__init__` (or a `--check` CLI flag) that validates every
   tool named in every loaded profile's `tool_instructions` actually
   resolves — cheap insurance against the exact failure mode this audit
   found twice.
