// Whiplash module -- polls /api/whiplash/state (proxying webapp/server.py's
// real /whiplash/state, or hud/demo_data.py's get_demo_whiplash_state() in
// --demo mode) and renders the metronome/MIDI/timing/Clone Hero panels.
// Same "init once, keep refreshing in the background" idiom as
// stats_hub.js's initStatsHub() rather than start/stop-on-tab-visibility.

let whiplashRefresh = null;
let whiplashInited = false;

function initWhiplashTab() {
  if (whiplashInited) return;
  whiplashInited = true;
  loadWhiplashState();
  whiplashRefresh = setInterval(loadWhiplashState, 2000);
}

async function loadWhiplashState() {
  try {
    const data = await fetch(`${BASE}/api/whiplash/state`).then(r => r.json());
    if (data.error) return;

    const m = data.metronome || {};
    document.getElementById('wh-bpm').textContent = m.running ? `${m.bpm} BPM` : '--';
    document.getElementById('wh-synced').textContent = m.running
      ? (m.synced ? 'Synced' : 'Not synced')
      : 'Metronome stopped';

    const g = data.groove || {};
    document.getElementById('wh-groove').textContent = g.active
      ? `${g.name} -- ${g.artist_credit}`
      : 'No groove active';

    const midi = data.midi || {};
    const midiEl = document.getElementById('wh-midi-status');
    if (midi.running) {
      midiEl.textContent = `Connected -- ${midi.port} (${midi.hit_count} hits)`;
    } else {
      midiEl.textContent = midi.available ? 'Not connected' : 'python-rtmidi not installed';
    }
    const hits = midi.last_hits || [];
    document.getElementById('wh-recent-hits').textContent = hits.length
      ? 'Recent: ' + hits.map(h => `${h.piece} (${h.velocity})`).join(', ')
      : '';

    const stats = data.timing_stats || {};
    document.getElementById('wh-pocket-pct').textContent =
      stats.count ? `${stats.pocket_pct}%` : '--';
    document.getElementById('wh-timing-detail').textContent = stats.count
      ? `${stats.count} hits -- avg ${stats.avg_abs_deviation_ms}ms off grid -- `
        + `${stats.rushing_count} rushing / ${stats.dragging_count} dragging`
      : 'No hits scored yet';

    const ch = data.clone_hero || {};
    document.getElementById('wh-clone-hero').textContent = ch.song
      ? `${ch.artist} -- ${ch.song}`
      : 'No song detected';
  } catch (e) {
    // Leave the last-known state showing rather than flashing to offline
    // on a single dropped poll.
  }
}
