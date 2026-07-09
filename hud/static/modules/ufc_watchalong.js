// UFC Watchalong module -- populates the Status tab's Watchalong panel.
// See f1_watchalong.js for the F1 half and the shared loadWatchalongStatus()
// orchestrator that calls both.

async function loadUFCWatchalongStatus() {
  const dot = document.getElementById('wa-ufc-dot');
  const text = document.getElementById('wa-ufc-text');
  if (!dot || !text) return;

  try {
    const s = await fetch('/api/ufc/state').then(r => r.json());
    dot.classList.toggle('live', !!s.active);
    text.textContent = s.active
      ? (s.main_event || 'Live event')
      : (s.event_name ? `${s.event_name} (not live)` : 'No event tonight');
  } catch(e) {
    text.textContent = '--';
  }
}
