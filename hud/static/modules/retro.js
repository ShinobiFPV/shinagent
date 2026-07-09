// Retro Gaming module -- Q2 as Player 2 via RetroArch + vgamepad.
// hud.js's showTab() calls loadRetroTab() when this tab is opened (see the
// dispatch line added there); everything else lives here.

let _retroAllGames = [];
let _retroFiltered = [];
let _retroCurrentSystem = '';
let _retroAiActive = false;
let _retroPollInterval = null;

function loadRetroTab() {
  loadRetroStatus();
  loadRetroGames();
  if (!_retroPollInterval) {
    _retroPollInterval = setInterval(loadRetroStatus, 5000);
  }
}

async function loadRetroStatus() {
  try {
    const data = await fetch('/api/retro/status').then(r => r.json());

    if (data.platform_warning) {
      document.getElementById('retro-platform-warn').style.display = 'block';
      document.getElementById('retro-content').style.display = 'none';
      return;
    }
    document.getElementById('retro-platform-warn').style.display = 'none';
    document.getElementById('retro-content').style.display = 'block';

    const raStatus = document.getElementById('ra-status');
    raStatus.textContent = data.ra_connected ? 'CONNECTED' :
      (data.retroarch_found ? 'NOT RUNNING' : 'NOT FOUND');
    raStatus.className = 'data-value ' + (data.ra_connected ? 'good' : 'alert');

    document.getElementById('ra-game').textContent = data.current_game || '--';

    const p2 = document.getElementById('ra-p2');
    p2.textContent = data.p2_available ? 'READY' : 'NOT INSTALLED';
    p2.className = 'data-value ' + (data.p2_available ? 'good' : 'warn');
  } catch(e) { /* keep last-known state on a dropped poll */ }
}

async function loadRetroGames() {
  try {
    const data = await fetch('/api/retro/games').then(r => r.json());
    _retroAllGames = data.games || [];
    renderRetroGames();
  } catch(e) {}
}

function filterSystem(system) {
  _retroCurrentSystem = system;
  document.querySelectorAll('.system-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.system === system);
  });
  renderRetroGames();
}

function filterGames() {
  renderRetroGames();
}

function renderRetroGames() {
  const search = document.getElementById('retro-search')?.value.toLowerCase() || '';
  const list = document.getElementById('retro-game-list');
  if (!list) return;

  const SYSTEM_COLORS = { nes: '#ff3c3c', snes: '#7c3aed', genesis: '#0ea5e9' };

  _retroFiltered = _retroAllGames.filter(g => {
    const matchSystem = !_retroCurrentSystem || g.system === _retroCurrentSystem;
    const matchSearch = !search || g.name.toLowerCase().includes(search);
    return matchSystem && matchSearch;
  });

  if (!_retroFiltered.length) {
    list.innerHTML = '<div style="color:rgba(0,200,120,0.3);font-size:11px;' +
                     'padding:8px">No games found. Add ROMs to ~/ROMs/</div>';
    return;
  }

  // Indices into _retroFiltered (not embedded JSON) are passed to the click
  // handlers below -- embedding a JSON.stringify'd object directly inside a
  // double-quoted onclick="..." attribute breaks as soon as the object
  // itself contains a double-quote character (which JSON.stringify's own
  // string escaping produces), silently truncating the attribute. An index
  // lookup avoids the whole class of problem, same as the ACC setup cards
  // (onclick="applySetup(${s.id})") do elsewhere in this app.
  list.innerHTML = _retroFiltered.map((g, i) => `
    <div class="game-item" onclick="selectRetroGame(${i})">
      <span class="game-system-badge"
            style="background:${SYSTEM_COLORS[g.system] || '#444'}22;
            color:${SYSTEM_COLORS[g.system] || '#888'};
            border:1px solid ${SYSTEM_COLORS[g.system] || '#444'}44">
        ${g.system.toUpperCase()}
      </span>
      <span class="game-name">${escapeRetroHtml(g.name)}</span>
      <button class="launch-btn" onclick="event.stopPropagation();launchRetroGame(${i})">
        LAUNCH
      </button>
    </div>
  `).join('');
}

function escapeRetroHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

let _retroSelectedGame = null;

function selectRetroGame(index) {
  _retroSelectedGame = _retroFiltered[index] || null;
}

async function launchRetroGame(index) {
  const game = _retroFiltered[index];
  if (!game) return;
  _retroSelectedGame = game;

  const btn = event.target;
  const originalText = btn.textContent;
  btn.textContent = 'LAUNCHING...';
  btn.disabled = true;

  try {
    const r = await fetch('/api/retro/launch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: game.path, system: game.system})
    }).then(r => r.json());

    if (r.ok) {
      showToast(`Launched ${game.name}`);
      document.getElementById('ra-game').textContent = game.name;
      if (!r.p2_available) {
        showToast('Install vgamepad for Q2 Player 2 control', 'error');
      }
    } else {
      showToast(r.error || 'Launch failed', 'error');
    }
  } catch(e) {
    showToast('Could not reach HUD server', 'error');
  }

  btn.textContent = originalText;
  btn.disabled = false;
}

async function toggleAI() {
  const btn = document.getElementById('ai-toggle-btn');
  const status = document.getElementById('ai-status');

  if (_retroAiActive) {
    await fetch('/api/retro/ai/stop', {method: 'POST'});
    _retroAiActive = false;
    btn.textContent = 'ENABLE Q2 P2';
    btn.style.borderColor = 'rgba(0,220,120,0.3)';
    btn.style.color = '#00dc78';
    status.textContent = 'Q2 standing by';
  } else {
    const mode = document.getElementById('ai-mode').value;
    const aggression = parseInt(document.getElementById('ai-aggression').value, 10) / 100;

    const r = await fetch('/api/retro/ai/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode, aggression})
    }).then(r => r.json());

    if (r.ok) {
      _retroAiActive = true;
      btn.textContent = 'DISABLE Q2 P2';
      btn.style.borderColor = 'rgba(255,60,60,0.4)';
      btn.style.color = '#ff3c3c';
      status.textContent = `Q2 playing as P2 (${mode} mode)`;
    } else {
      showToast(r.error || 'Could not start AI', 'error');
    }
  }
}

function updateAggression(val) {
  document.getElementById('aggression-val').textContent = val + '%';
  updateAIConfig();
}

async function updateAIConfig() {
  if (!_retroAiActive) return;
  const mode = document.getElementById('ai-mode').value;
  const aggression = parseInt(document.getElementById('ai-aggression').value, 10) / 100;

  await fetch('/api/retro/ai/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode, aggression})
  });
}

async function p2Press(button) {
  await fetch('/api/retro/p2/press', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({button, duration_ms: 120})
  });
}

// True press-and-hold (not a fixed-duration fake): mousedown calls
// VirtualP2Controller.hold() via /api/retro/p2/hold, mouseup calls
// release() via /api/retro/p2/release -- matters for platformers where
// held-vs-tapped jump height/run distance genuinely differs.
async function p2Hold(button) {
  await fetch('/api/retro/p2/hold', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({button})
  });
}

async function p2Release(button) {
  await fetch('/api/retro/p2/release', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({button})
  });
}

async function sendRA(command) {
  await fetch('/api/retro/control', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command})
  });
}

// Poll AI state for commentary/last-action feedback while active
setInterval(async () => {
  if (!_retroAiActive) return;
  try {
    const data = await fetch('/api/retro/ai/state').then(r => r.json());
    if (data.active && data.recent_actions?.length) {
      const lastAction = data.recent_actions.slice(-1)[0];
      if (lastAction?.length) {
        document.getElementById('ai-status').textContent =
          `Q2 pressed: ${lastAction.join('+')}`;
      }
    }
  } catch(e) {}
}, 1000);
