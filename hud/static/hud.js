// ── Config ────────────────────────────────────────────────────
const BASE = '';  // hud.js is served by the same Flask app it calls

// ── State ─────────────────────────────────────────────────────
let currentModule = 'default';
let isOnTop = false;
let isBorderless = false;
let toolbarHidden = false;
let pollInterval = null;
const POLL_MS = 1500;

// ── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  startPolling();
  loadACCSetups();
  loadBridgeStatus();
  setInterval(loadBridgeStatus, 5000);
  setInterval(detectGames, 8000);
  detectGames();
});

// ── Polling ───────────────────────────────────────────────────
function startPolling() {
  pollInterval = setInterval(pollState, POLL_MS);
  pollState();
}

async function pollState() {
  try {
    const data = await fetch(`${BASE}/api/state`).then(r => r.json());
    updateStatus(data);
    updateTelemetry(data.telemetry);
    updateModuleIndicator(data.module, data.q2_state);
  } catch(e) {
    updateConnectionStatus(false);
  }
}

// ── Status updates ────────────────────────────────────────────
function updateStatus(data) {
  updateDemoBanner(data);

  const connected = data.connected && !data.error;

  document.getElementById('s-connected').textContent =
    connected ? 'YES' : 'OFFLINE';
  document.getElementById('s-connected').className =
    'data-value ' + (connected ? 'good' : 'alert');

  const state = data.q2_state || {};
  let stateStr = 'READY';
  if (state.thinking)  stateStr = 'THINKING';
  if (state.listening) stateStr = 'LISTENING';
  if (state.speaking)  stateStr = 'SPEAKING';

  document.getElementById('s-q2state').textContent = stateStr;

  const profile = (data.profile || '').split('/').pop().replace('.yaml','');
  document.getElementById('s-profile').textContent =
    (profile || '--').replace(/_/g, ' ').toUpperCase();
  document.getElementById('module-name').textContent =
    (profile || 'connecting...').replace(/_/g, ' ').toUpperCase();
  document.getElementById('llm-badge').textContent =
    (data.llm_backend || '--').toUpperCase();
  document.getElementById('s-backend').textContent =
    (data.llm_backend || '--').toUpperCase();

  currentModule = data.module || 'default';

  // Show/hide the First Officer flight-data panel for the active module
  const foPanel = document.getElementById('fo-panel');
  if (foPanel) {
    foPanel.style.display = currentModule === 'first_officer' ? 'block' : 'none';
    if (currentModule === 'first_officer' && typeof loadFirstOfficerTelemetry === 'function') {
      loadFirstOfficerTelemetry();
    }
  }
}

function updateConnectionStatus(connected) {
  document.getElementById('s-connected').textContent =
    connected ? 'YES' : 'OFFLINE';
  document.getElementById('s-connected').className =
    'data-value ' + (connected ? 'good' : 'alert');
}

// ── Demo mode banner ──────────────────────────────────────────
let _demoBannerShown = false;

function updateDemoBanner(data) {
  const banner = document.getElementById('demo-banner');
  if (!banner) return;

  if (data.demo_mode) {
    banner.style.display = 'block';
    document.getElementById('demo-module-label').textContent = data.module || 'unknown';
    if (!_demoBannerShown) {
      _demoBannerShown = true;
      const toolbar = document.getElementById('toolbar');
      if (toolbar) toolbar.style.marginTop = '24px';
    }
  } else if (_demoBannerShown) {
    _demoBannerShown = false;
    banner.style.display = 'none';
    const toolbar = document.getElementById('toolbar');
    if (toolbar) toolbar.style.marginTop = '';
  }
}

function showDemoSwitcher() {
  const existing = document.getElementById('demo-switcher-overlay');
  if (existing) { existing.remove(); return; }

  const modules = [
    'race_engineer', 'freeroam', 'first_officer', 'ship_computer',
    'f1_watchalong', 'ufc_watchalong', 'popup_video', 'whiplash',
    'beavis_butthead', 'circuit_builder', 'retro',
  ];

  const overlay = document.createElement('div');
  overlay.id = 'demo-switcher-overlay';
  overlay.style.cssText = `
    position:fixed;top:28px;left:50%;transform:translateX(-50%);
    background:rgba(0,8,10,0.97);
    border:1px solid rgba(0,220,120,0.3);
    border-radius:6px;padding:10px;z-index:10001;
    display:flex;gap:6px;flex-wrap:wrap;max-width:400px;
  `;

  modules.forEach(m => {
    const btn = document.createElement('button');
    btn.textContent = m.replace(/_/g, ' ').toUpperCase();
    btn.style.cssText = `
      padding:6px 10px;font-size:10px;cursor:pointer;
      background:rgba(0,220,120,0.08);
      border:1px solid rgba(0,220,120,0.2);
      color:#00dc78;border-radius:3px;font-family:inherit;
    `;
    btn.onclick = () => {
      fetch('/api/demo/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ module: m }),
      }).then(() => location.reload());
      overlay.remove();
    };
    overlay.appendChild(btn);
  });

  document.body.appendChild(overlay);
  setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 5000);
}

// ── Telemetry updates ─────────────────────────────────────────
// Shape matches face/server.py's _telemetry_status() -- a single
// normalized dict combining AC (preferred) or Forza, whichever is live.
// AC's tyre_wear_* fields are real percentages; Forza has no tyre-wear
// telemetry at all, so those come through as null and render as "--".
function updateTelemetry(t) {
  if (!t) {
    ['speed','gear','rpm','fuel','pos','cur','last','best','flag','lap']
      .forEach(id => {
        const el = document.getElementById('t-' + id);
        if (el) el.textContent = '--';
      });
    return;
  }

  const set = (id, val, cls) => {
    const el = document.getElementById('t-' + id);
    if (!el) return;
    el.textContent = val;
    el.className = 'data-value' + (cls ? ' ' + cls : '');
  };

  set('speed', t.speed_kmh != null ? Math.round(t.speed_kmh) + ' km/h' : '--');
  set('gear',  t.gear != null ? (t.gear === 0 ? 'N' : t.gear) : '--');
  set('rpm',   t.rpm != null ? Math.round(t.rpm / 100) * 100 : '--');

  const fuelCls = t.fuel_pct == null ? '' : t.fuel_pct < 10 ? 'alert' : t.fuel_pct < 25 ? 'warn' : '';
  set('fuel', t.fuel_pct != null ? Math.round(t.fuel_pct) + '%'
              : t.fuel_laps != null ? t.fuel_laps.toFixed(1) + ' laps' : '--', fuelCls);

  set('pos', t.position ? 'P' + t.position : '--');

  const fmtLap = ms => {
    if (!ms || ms <= 0) return '--:--.---';
    const m = Math.floor(ms / 60000);
    const s = ((ms % 60000) / 1000).toFixed(3).padStart(6, '0');
    return `${m}:${s}`;
  };
  set('cur',  fmtLap(t.current_lap_ms));
  set('last', fmtLap(t.last_lap_ms));
  set('best', fmtLap(t.best_lap_ms), 'good');
  set('flag', t.flag || 'None', t.flag && t.flag !== 'None' ? 'warn' : '');
  set('lap', t.lap != null ? t.lap : '--');

  document.getElementById('t-compound').textContent =
    t.compound ? t.compound.toUpperCase() : (t.source || '').toUpperCase();

  const tyreTemp = (id, temp, wear) => {
    const el = document.getElementById('t-' + id);
    const wearEl = document.getElementById('t-' + id + '-wear');
    if (el) {
      if (temp == null) {
        el.textContent = '--';
        el.style.color = '';
      } else {
        el.textContent = Math.round(temp) + 'C';
        el.style.color = temp < 60 ? '#4488ff' :
                         temp > 110 ? '#ff3c3c' : '#00dc78';
      }
    }
    if (wearEl) {
      wearEl.textContent = wear != null ? Math.round(wear * 100) + '%' : '';
    }
  };
  tyreTemp('fl', t.tyre_temp_fl, t.tyre_wear_fl);
  tyreTemp('fr', t.tyre_temp_fr, t.tyre_wear_fr);
  tyreTemp('rl', t.tyre_temp_rl, t.tyre_wear_rl);
  tyreTemp('rr', t.tyre_temp_rr, t.tyre_wear_rr);
}

// ── Module indicator (canvas-based themed dot) ────────────────
const INDICATORS = {
  default: drawDefaultIndicator,
  guest: drawDefaultIndicator,
  race_engineer: drawRaceEngineerIndicator,
  first_officer: drawFirstOfficerIndicator,
  ship_computer: drawShipComputerIndicator,
  watchalong: drawWatchalongIndicator,
  popup_video: drawPopupIndicator,
};

let _indicatorPhase = 0;
let _indicatorState = { speaking: false, listening: false, thinking: false };

function updateModuleIndicator(module, state) {
  _indicatorState = state || {};
  currentModule = module || 'default';
}

// Animate the indicator at 30fps independently of the 1.5s state poll
setInterval(() => {
  _indicatorPhase += 0.05;
  const canvas = document.getElementById('module-indicator');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, 28, 28);

  const drawFn = INDICATORS[currentModule] || drawDefaultIndicator;
  drawFn(ctx, 28, _indicatorPhase, _indicatorState);
}, 33);

function drawDefaultIndicator(ctx, size, phase, state) {
  const cx = size/2, cy = size/2, r = 7;
  const pulse = 0.5 + 0.5 * Math.sin(phase);

  let color, alpha;
  if (state.speaking)  { color = '255,60,60'; alpha = 0.7 + pulse * 0.3; }
  else if (state.thinking) { color = '0,200,255'; alpha = 0.4 + pulse * 0.5; }
  else if (state.listening){ color = '0,220,120'; alpha = 0.7 + pulse * 0.3; }
  else { color = '0,220,120'; alpha = 0.3 + pulse * 0.2; }

  ctx.beginPath();
  ctx.arc(cx, cy, r + 2, 0, Math.PI*2);
  ctx.strokeStyle = `rgba(${color},${alpha * 0.3})`;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.6, 0, Math.PI*2);
  ctx.fillStyle = `rgba(${color},${alpha})`;
  ctx.fill();
}

function drawRaceEngineerIndicator(ctx, size, phase, state) {
  const cx = size/2, cy = size/2 + 2;
  const r = 10;
  const startAngle = Math.PI * 0.75;
  const endAngle   = Math.PI * 2.25;
  const range = endAngle - startAngle;

  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, endAngle);
  ctx.strokeStyle = 'rgba(255, 60, 60, 0.15)';
  ctx.lineWidth = 3;
  ctx.stroke();

  const fill = state.speaking ?
    (0.5 + 0.5 * Math.sin(phase * 4)) :
    (0.3 + 0.3 * Math.sin(phase));
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, startAngle + range * fill);
  ctx.strokeStyle = state.thinking ?
    `rgba(0, 200, 255, 0.9)` : `rgba(255, 60, 60, 0.9)`;
  ctx.lineWidth = 3;
  ctx.lineCap = 'round';
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(cx, cy, 2.5, 0, Math.PI*2);
  ctx.fillStyle = 'rgba(255, 60, 60, 0.8)';
  ctx.fill();
}

function drawFirstOfficerIndicator(ctx, size, phase, state) {
  const cx = size/2, cy = size/2, r = 11;

  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI*2);
  ctx.clip();

  ctx.fillStyle = 'rgba(0, 80, 180, 0.7)';
  ctx.fillRect(cx - r, cy - r, r*2, r);

  ctx.fillStyle = 'rgba(150, 80, 30, 0.7)';
  ctx.fillRect(cx - r, cy, r*2, r);

  ctx.restore();

  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI*2);
  ctx.strokeStyle = state.speaking ? 'rgba(0, 200, 255, 0.9)' :
                                     'rgba(0, 150, 255, 0.5)';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(cx - r + 2, cy);
  ctx.lineTo(cx + r - 2, cy);
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawShipComputerIndicator(ctx, size, phase, state) {
  const cx = size/2, cy = size/2, r = 11;

  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI*2);
  ctx.strokeStyle = 'rgba(255, 140, 0, 0.2)';
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy);
  ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r);
  ctx.strokeStyle = 'rgba(255, 140, 0, 0.1)';
  ctx.lineWidth = 0.5;
  ctx.stroke();

  const sweepAngle = phase * 2;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.cos(sweepAngle) * r,
             cy + Math.sin(sweepAngle) * r);
  ctx.strokeStyle = 'rgba(255, 140, 0, 0.9)';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  const trailStart = sweepAngle - 1.0;
  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.7, trailStart, sweepAngle);
  ctx.strokeStyle = 'rgba(255, 140, 0, 0.25)';
  ctx.lineWidth = 4;
  ctx.stroke();
}

function drawWatchalongIndicator(ctx, size, phase, state) {
  const cx = size/2, cy = size/2, r = 11;

  const pulse = 0.5 + 0.5 * Math.sin(phase * 2);
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI*2);
  ctx.strokeStyle = `rgba(200, 60, 60, ${0.3 + pulse * 0.5})`;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(cx, cy, 3, 0, Math.PI*2);
  ctx.fillStyle = `rgba(200, 60, 60, ${0.6 + pulse * 0.4})`;
  ctx.fill();
}

function drawPopupIndicator(ctx, size, phase, state) {
  const cx = size/2, cy = size/2;
  const tick = Math.floor(phase * 2) % 2;

  ctx.strokeStyle = 'rgba(200, 200, 200, 0.4)';
  ctx.lineWidth = 1;
  ctx.strokeRect(cx - 8, cy - 6, 16, 12);

  const holeColor = tick ? 'rgba(255,255,200,0.6)' : 'rgba(100,100,80,0.3)';
  [cy - 9, cy + 3].forEach(hy => {
    [-5, 5].forEach(hx => {
      ctx.beginPath();
      ctx.arc(cx + hx, hy, 2, 0, Math.PI*2);
      ctx.fillStyle = holeColor;
      ctx.fill();
    });
  });

  ctx.fillStyle = 'rgba(255, 220, 0, 0.7)';
  ctx.beginPath();
  ctx.moveTo(cx - 2, cy - 3);
  ctx.lineTo(cx + 4, cy);
  ctx.lineTo(cx - 2, cy + 3);
  ctx.closePath();
  ctx.fill();
}

// ── Tab navigation ─────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.pane').forEach(p =>
    p.style.display = 'none');
  document.querySelectorAll('.nav-tab').forEach(t =>
    t.classList.remove('active'));

  document.getElementById('pane-' + name).style.display = 'block';
  document.getElementById('tab-' + name).classList.add('active');

  if (name === 'acc')      loadACCSetups();
  if (name === 'ed')       loadEDState();
  if (name === 'popup')    loadPopupState();
  if (name === 'bridges')  loadBridgeStatus();
  if (name === 'status' && typeof loadWatchalongStatus === 'function') loadWatchalongStatus();
  if (name === 'retro' && typeof loadRetroTab === 'function') loadRetroTab();
  if (name === 'stats' && typeof initStatsHub === 'function') initStatsHub();
  if (name === 'whiplash' && typeof initWhiplashTab === 'function') initWhiplashTab();
  if (name === 'bb' && typeof initBB === 'function') initBB();
  if (name === 'circuit' && typeof initCircuitBuilder === 'function') initCircuitBuilder();
  if (name === 'game' && typeof initGameTab === 'function') initGameTab();
}

// ── Toolbar ────────────────────────────────────────────────────
function toggleToolbar() {
  toolbarHidden = !toolbarHidden;
  const tb = document.getElementById('toolbar');
  tb.classList.toggle('hidden', toolbarHidden);
}

function toggleOnTop() {
  if (typeof pywebview !== 'undefined') {
    pywebview.api.toggle_always_on_top().then(val => {
      isOnTop = val;
      document.getElementById('ontop-btn').classList.toggle('active', val);
    });
  }
}

function toggleBorderless() {
  isBorderless = !isBorderless;
  document.body.classList.toggle('borderless', isBorderless);
}

// ── ACC Setups (core actions; module extends with delete) ──────
async function loadACCSetups() {
  const car   = document.getElementById('acc-filter-car')?.value || '';
  const track = document.getElementById('acc-filter-track')?.value || '';
  const list  = document.getElementById('acc-setup-list');
  if (!list) return;

  try {
    const params = new URLSearchParams();
    if (car)   params.set('car', car);
    if (track) params.set('track', track);
    const data = await fetch(`${BASE}/api/acc/setups?${params}`)
                       .then(r => r.json());

    if (!data.ok || !data.setups?.length) {
      list.innerHTML = '<div style="color:rgba(0,200,120,0.3);font-size:11px">' +
                       'No setups saved yet. Generate one!</div>';
      return;
    }

    list.innerHTML = data.setups.map(s => `
      <div class="setup-card">
        <div class="setup-info">
          <div class="setup-name">${s.name}</div>
          <div class="setup-meta">
            ${s.car} &middot; ${s.track} &middot; ${s.session_type} &middot; ${s.weather}
            ${s.applied_at ? '&middot; Applied ' + timeAgo(s.applied_at) : '&middot; Never applied'}
          </div>
        </div>
        <button class="apply-btn" onclick="applySetup(${s.id})">APPLY</button>
        <button class="delete-btn" onclick="deleteSetup(${s.id})">DEL</button>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = '<div style="color:#ff3c3c;font-size:11px">' +
                     'Could not reach Q2</div>';
  }
}

async function applySetup(id) {
  const r = await fetch(`${BASE}/api/acc/apply/${id}`,
                        {method:'POST'}).then(r => r.json());
  if (r.ok) {
    showToast('Setup applied to ACC');
    loadACCSetups();
  } else {
    showToast('Apply failed: ' + (r.error || 'unknown'), 'error');
  }
}

async function generateSetup() {
  const btn = document.getElementById('gen-btn');
  const status = document.getElementById('gen-status');
  btn.disabled = true;
  btn.textContent = 'GENERATING...';
  status.textContent = 'Researching meta and generating setup...';

  try {
    const data = {
      car:          document.getElementById('gen-car').value,
      track:        document.getElementById('gen-track').value,
      session_type: document.getElementById('gen-session').value,
      weather:      document.getElementById('gen-weather').value,
    };

    const r = await fetch(`${BASE}/api/acc/generate`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    }).then(r => r.json());

    if (r.ok) {
      status.textContent = r.message || 'Setup generated and applied!';
      status.style.color = '#00dc78';
      loadACCSetups();
    } else {
      status.textContent = 'Error: ' + (r.error || 'unknown');
      status.style.color = '#ff3c3c';
    }
  } catch(e) {
    status.textContent = 'Could not reach Q2';
    status.style.color = '#ff3c3c';
  }

  btn.disabled = false;
  btn.textContent = 'GENERATE SETUP';
}

// ── ED Ship Computer (core actions; module extends with search) ─
async function loadEDState() {
  try {
    const data = await fetch(`${BASE}/api/ed/state`).then(r => r.json());
    if (!data.state) return;

    const s = data.state;
    const set = (id, val) => {
      const el = document.getElementById('ed-' + id);
      if (el) el.textContent = val || '--';
    };

    set('cmdr',     s.commander);
    set('ship',     s.ship);
    set('system',   s.location?.system);
    set('location', s.location?.station || s.location?.body ||
                    (s.location?.supercruise ? 'Supercruise' : '--'));

    const fuel = s.fuel;
    if (fuel) {
      const pct = Math.round(fuel.main / fuel.capacity * 100);
      set('fuel', `${fuel.main.toFixed(1)}T (${pct}%)`);
    }
    set('legal', s.status?.legal_state);

    const eventsEl = document.getElementById('ed-events');
    if (eventsEl && s.recent_events?.length) {
      eventsEl.innerHTML = s.recent_events.slice(0, 10).map(e => `
        <div class="ed-event">
          <span class="ed-event-time">${e.time}</span>
          <span class="ed-event-text">${e.text}</span>
        </div>
      `).join('');
    }
  } catch(e) {}
}

async function sendEDPaste() {
  const text = document.getElementById('ed-paste-input').value.trim();
  if (!text) return;

  const responseEl = document.getElementById('ed-paste-response');
  responseEl.style.display = 'block';
  responseEl.textContent = 'Sending to Q2...';

  try {
    const r = await fetch(`${BASE}/api/ed/paste`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})
    }).then(r => r.json());

    responseEl.textContent = r.response || r.error || 'Sent.';
    document.getElementById('ed-paste-input').value = '';
  } catch(e) {
    responseEl.textContent = 'Could not reach Q2';
  }
}

function openINARA() {
  const text = document.getElementById('ed-paste-input').value.trim();
  const url = text
    ? `https://inara.cz/elite/starsystem/?search=${encodeURIComponent(text)}`
    : 'https://inara.cz';
  openExternal(url);
}

function openEDSM() {
  const text = document.getElementById('ed-paste-input').value.trim();
  const url = text
    ? `https://www.edsm.net/en/system/id/0/name/${encodeURIComponent(text)}`
    : 'https://www.edsm.net';
  openExternal(url);
}

// ── Pop-Up Video (core actions; module owns auto-advance) ───────
async function loadPopupState() {
  try {
    const data = await fetch(`${BASE}/api/popup/state`).then(r => r.json());
    if (data.title) {
      document.getElementById('popup-title').textContent =
        data.title + (data.year ? ` (${data.year})` : '');
      document.getElementById('popup-count').textContent =
        `${data.popup_count || 0} pop-ups generated`;
    }
    if (data.last_delivered) {
      showPopupBubble(data.last_delivered);
    }
  } catch(e) {}

  try {
    const u = await fetch(`${BASE}/api/popup/upcoming`).then(r => r.json());
    if (u.active && u.upcoming) {
      document.getElementById('popup-upcoming').innerHTML =
        u.upcoming.map(p => `
          <div style="padding:5px 0;border-bottom:1px solid rgba(0,220,120,0.05)">
            <span style="color:rgba(0,200,120,0.5);font-size:10px">
              ${p.timestamp_display}</span>
            <span style="font-size:11px;color:#c8f0dc;margin-left:8px">
              ${p.title}</span>
          </div>
        `).join('') || '--';
    }
  } catch(e) {}
}

async function getPopup() {
  const ts = document.getElementById('popup-ts').value.trim();
  if (!ts) return;

  try {
    const r = await fetch(`${BASE}/api/popup/timestamp`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({timestamp: ts})
    }).then(r => r.json());

    if (r.ok) loadPopupState();
  } catch(e) {}
}

const BUBBLE_COLOURS = {
  FACT:       '#fff9c4',
  CAST:       '#e3f2fd',
  MUSIC:      '#f3e5f5',
  LOCATION:   '#e8f5e9',
  TECH:       '#fff3e0',
  CORRECTION: '#ffebee',
  HISTORY:    '#f5f5f5',
  EASTER_EGG: '#fffde7',
};

function showPopupBubble(popup) {
  const area = document.getElementById('popup-bubble-area');
  if (!area) return;

  const color = BUBBLE_COLOURS[popup.type] || '#f5f5f5';
  const div = document.createElement('div');
  div.className = 'popup-bubble';
  div.style.background = color;
  div.innerHTML = `
    <div class="bubble-type">${popup.type}</div>
    <div class="bubble-title">${popup.title}</div>
    <div class="bubble-body">${popup.body}</div>
  `;

  area.insertBefore(div, area.firstChild);

  setTimeout(() => {
    div.style.transition = 'opacity 0.5s';
    div.style.opacity = '0';
    setTimeout(() => div.remove(), 500);
  }, 15000);
}

// ── Bridge management ──────────────────────────────────────────
async function loadBridgeStatus() {
  try {
    const data = await fetch(`${BASE}/api/bridges/status`)
                       .then(r => r.json());
    const list = document.getElementById('bridge-list');
    if (!list) return;

    if (data.platform_warning) {
      list.innerHTML = `<div class="platform-warn">${data.message}</div>`;
      return;
    }

    list.innerHTML = Object.entries(data.bridges).map(([key, b]) => `
      <div class="bridge-pill">
        <div class="bridge-dot ${b.running ? 'running' : 'stopped'}"></div>
        <span class="bridge-name" style="color:${b.color}">${b.name}</span>
        <span style="font-size:10px;color:rgba(200,200,200,0.3)">
          :${b.port}
        </span>
        <button class="bridge-btn ${b.running ? 'stop' : 'start'}"
                onclick="${b.running
                  ? `stopBridge('${key}')`
                  : `startBridge('${key}')`}">
          ${b.running ? 'STOP' : 'START'}
        </button>
      </div>
    `).join('');
  } catch(e) {}
}

async function startBridge(name) {
  await fetch(`${BASE}/api/bridges/start/${name}`, {method:'POST'});
  setTimeout(loadBridgeStatus, 1000);
}

async function stopBridge(name) {
  await fetch(`${BASE}/api/bridges/stop/${name}`, {method:'POST'});
  setTimeout(loadBridgeStatus, 1000);
}

async function detectGames() {
  try {
    const data = await fetch(`${BASE}/api/games/detected`)
                       .then(r => r.json());

    const gamesEl = document.getElementById('s-games');
    const bridgeGamesEl = document.getElementById('bridge-games');

    if (!data.games?.length) {
      const msg = '<div style="color:rgba(0,200,120,0.3);font-size:11px">' +
                  'No supported games detected</div>';
      if (gamesEl) gamesEl.innerHTML = msg;
      if (bridgeGamesEl) bridgeGamesEl.innerHTML = msg;
      return;
    }

    const html = data.games.map(g => `
      <div class="game-badge">
        <span>&#9679;</span>
        <span>${g.name}</span>
        ${g.bridge ? `<span style="opacity:0.5">&rarr; ${g.bridge} bridge</span>` : ''}
      </div>
    `).join('');

    if (gamesEl) gamesEl.innerHTML = html;
    if (bridgeGamesEl) bridgeGamesEl.innerHTML = html;
  } catch(e) {}
}

async function autoStartBridges() {
  const data = await fetch(`${BASE}/api/games/detected`).then(r => r.json());
  for (const game of (data.games || [])) {
    if (game.bridge) await startBridge(game.bridge);
  }
  setTimeout(loadBridgeStatus, 2000);
}

// ── Web tab ────────────────────────────────────────────────────
function openExternal(url) {
  if (typeof pywebview !== 'undefined') {
    pywebview.api.open_url(url);
  } else {
    window.open(url, '_blank');
  }
}

function openQ2WebApp() {
  fetch(`${BASE}/api/state`).then(r => r.json()).then(d => {
    const url = `http://${d.q2_host || '192.168.1.203'}:8766`;
    openExternal(url);
  });
}

function openCustomURL() {
  const url = document.getElementById('web-url').value.trim();
  if (url) openExternal(url);
}

// ── Utilities ──────────────────────────────────────────────────
function showToast(msg, type = 'success') {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed;bottom:12px;right:12px;
    background:${type === 'error' ? '#ff3c3c' : '#00dc78'};
    color:#000;padding:8px 16px;border-radius:4px;
    font-size:11px;font-weight:700;z-index:9999;
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

function timeAgo(dateStr) {
  const d = new Date(dateStr);
  const diff = Date.now() - d.getTime();
  const hours = Math.floor(diff / 3600000);
  if (hours < 1)  return 'just now';
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours/24)}d ago`;
}
