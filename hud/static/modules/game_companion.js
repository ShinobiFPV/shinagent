// Game Companion module -- polls /api/game/session and /api/game/history
// (proxying face/server.py's /game/session and /game/history, or
// hud/demo_data.py's get_demo_game_* in --demo mode) and renders the
// session/progress/history panels. Same "init once, keep refreshing in
// the background" idiom as whiplash.js's initWhiplashTab().

let gameRefresh = null;
let gameInited = false;

const GAME_QUICK_ASKS = {
  action_rpg: ["I'm stuck on a boss", "Best build advice?", "Where do I go next?"],
  rpg:        ["Quest help", "Best class/build?", "Where next?"],
  survival:   ["What to craft next?", "Base building tips", "Resource priorities"],
  strategy:   ["Build order help", "Economy advice", "Counter this unit"],
  shooter:    ["Best loadout?", "Meta weapons now?", "Map tips"],
  puzzle:     ["I'm stuck on this puzzle", "Any secrets nearby?"],
  sports:     ["Best team/setup?", "Meta strategy?"],
  default:    ["I'm stuck", "General tips", "Where next?"],
};

function initGameTab() {
  if (gameInited) return;
  gameInited = true;
  loadGameSession();
  loadGameHistory();
  gameRefresh = setInterval(loadGameSession, 5000);
}

async function loadGameSession() {
  try {
    const r = await fetch(`${BASE}/api/game/session`).then(r => r.json());

    document.getElementById('game-name').textContent = r.active ? (r.game_name || '--') : '--';
    document.getElementById('game-genre').textContent = r.active ? (r.genre || '--').replace(/_/g, ' ') : '--';
    document.getElementById('game-char-info').textContent = r.character_info || '--';
    document.getElementById('game-area').textContent = r.current_area || '--';
    document.getElementById('game-stuck-on').textContent = r.stuck_on || '--';

    const progList = document.getElementById('game-progress-list');
    const notes = r.progress_notes || [];
    if (r.active && notes.length) {
      progList.innerHTML = notes.slice(-5).reverse()
        .map(n => `<div style="padding:3px 0;border-bottom:1px solid rgba(0,200,80,0.05)">- ${escapeHtml(n)}</div>`)
        .join('');
    } else if (r.active) {
      progList.textContent = 'No notes yet.';
    } else {
      progList.textContent = "No active game session. Tell Q2 which game you're playing to start.";
    }

    buildGameQuickAsks(r.active ? (r.genre || 'default') : 'default');
  } catch (e) {}
}

function buildGameQuickAsks(genre) {
  const container = document.getElementById('game-quick-btns');
  if (!container) return;
  const asks = GAME_QUICK_ASKS[genre] || GAME_QUICK_ASKS.default;
  container.innerHTML = asks.map(q => `
    <button style="padding:4px 10px;font-size:10px;background:rgba(0,200,80,0.06);
            border:1px solid rgba(0,220,120,0.2);color:#c8f0dc;border-radius:3px;
            cursor:pointer;font-family:inherit"
            onclick="gameQuickAsk('${q.replace(/'/g, "\\'")}')">${escapeHtml(q)}</button>
  `).join('');
}

async function gameAsk() {
  const input = document.getElementById('game-question-input');
  const q = input?.value.trim();
  if (!q) return;

  const responseEl = document.getElementById('game-ask-response');
  responseEl.style.display = 'block';
  responseEl.textContent = 'Sending to Q2...';

  try {
    const r = await fetch(`${BASE}/api/q2/chat`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: q}),
    }).then(r => r.json());
    responseEl.textContent = r.response || r.error || 'Sent.';
    input.value = '';
  } catch (e) {
    responseEl.textContent = 'Could not reach Q2';
  }
}

function gameQuickAsk(question) {
  const input = document.getElementById('game-question-input');
  if (input) {
    input.value = question;
    gameAsk();
  }
}

async function loadGameHistory() {
  try {
    const r = await fetch(`${BASE}/api/game/history`).then(r => r.json());
    const list = document.getElementById('game-history-list');
    if (!list) return;

    const history = r.history || [];
    if (!history.length) {
      list.textContent = 'No game history yet.';
      return;
    }

    list.innerHTML = history.slice(0, 6).map(g => `
      <div style="padding:4px 0;border-bottom:1px solid rgba(0,200,80,0.06)">
        <span style="color:#c8f0dc">${escapeHtml(g.game_name || 'Unknown')}</span>
        <span style="color:rgba(0,200,120,0.4);font-size:9px;margin-left:6px">
          ${escapeHtml((g.genre || '').replace(/_/g, ' '))}
        </span>
      </div>
    `).join('');
  } catch (e) {}
}
