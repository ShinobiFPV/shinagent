# Webapp — Low / Enhancement backlog

From the 2026-07-02 quality audit. Critical and Medium findings were fixed
directly (see git history for this file's commit). These are lower-priority
items left for a deliberate product decision or future pass.

- **Header chip buttons (`.hbtn`) are below the 44px tap-target guideline.**
  Current height is ~28px (font 0.82rem + 6px vertical padding). Bumping to
  44px would require a visual redesign of the header row (it's a horizontally
  scrolling strip of 6 secondary actions) — punting until there's appetite to
  redesign that row rather than just padding it out.

- **Failed TTS requests fail silently.** `speakReply()` logs a console
  warning and returns; the user still sees the text reply, so this degrades
  gracefully, but there's no visual cue that voice output didn't happen.
  Consider a small muted icon/toast on TTS failure instead of only console.warn.

- **`/camera/snapshot` is not called from `index.html`** — it's used
  server-side by `tools/photo_tools.py` (`capture_image()`), not the webapp UI.
  Not a bug, just noting it so it isn't mistaken for dead code later.

- **No app-layer authentication; `CORS(app)` allows all origins.** Today's
  security perimeter is the home LAN / Tailscale network, not the app itself.
  Fine for a single-user device, but if this port is ever exposed beyond that
  perimeter (router port-forward, etc.), anyone who can reach it can chat as
  Q2, flip the LLM backend, or trigger a restart. If that changes, add a
  shared-secret header check in a `before_request` hook.

- **`config.yaml` comments get stripped on every `/voice` or `/llm-switch`
  write** — both handlers round-trip the file through `yaml.safe_load` /
  `yaml.dump`, which doesn't preserve comments. Only 3 comment lines exist
  today so the blast radius is small; a `ruamel.yaml` round-trip loader would
  fix it properly if that becomes annoying.

- **Polling instead of push** — the face-color sync (2s interval, only while
  face mode is open) and the post-restart health poll (1s, capped at 30
  tries) are both short-lived and bounded, so SSE/WebSocket isn't worth the
  added complexity at current scale. Revisit if more real-time state gets
  added.

- **Service worker cache-busting is manual** (`CACHE_NAME` version string in
  `sw.js`). There's no build step in this repo (per CLAUDE.md), so there's
  nothing to hash automatically — just remember to bump it whenever
  `index.html` or any pre-cached static asset changes.

- **LLM model ID lists are hand-maintained in two places** —
  `config/config.yaml` (actual configured models) and
  `WEBAPP_LLM_MODELS`/`WEBAPP_LLM_LABELS` in `index.html` (the switcher's
  dropdown options). They agree today; nothing enforces that they keep
  agreeing as models change.
