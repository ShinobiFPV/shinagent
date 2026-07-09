// F1 Watchalong module -- populates the Status tab's Watchalong panel.
// Also defines the shared loadWatchalongStatus() orchestrator that hud.js
// calls (from showTab('status')) and that periodically polls both F1 and
// UFC status; see ufc_watchalong.js for the UFC half.

async function loadF1WatchalongStatus() {
  const dot = document.getElementById('wa-f1-dot');
  const text = document.getElementById('wa-f1-text');
  if (!dot || !text) return;

  try {
    const s = await fetch('/api/f1/state').then(r => r.json());
    dot.classList.toggle('live', !!s.active);
    text.textContent = s.active
      ? (s.session_name || 'Live session')
      : (s.session_name ? `${s.session_name} (not live)` : 'No live session');
  } catch(e) {
    text.textContent = '--';
  }
}

// Shared orchestrator -- called by hud.js's showTab() and on a slow
// interval here (F1/UFC status are network calls to OpenF1/ESPN on the Q2
// side, deliberately not part of the fast 1.5s /api/state poll).
async function loadWatchalongStatus() {
  await Promise.all([
    loadF1WatchalongStatus(),
    typeof loadUFCWatchalongStatus === 'function' ? loadUFCWatchalongStatus() : Promise.resolve(),
  ]);
}

document.addEventListener('DOMContentLoaded', () => {
  loadWatchalongStatus();
  setInterval(loadWatchalongStatus, 15000);
});
