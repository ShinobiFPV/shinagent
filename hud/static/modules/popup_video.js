// Pop-Up Video module -- owns the auto-advance timer referenced by
// index.html's checkbox. Mirrors webapp/popup_companion.html's own
// client-side auto-advance implementation: once playback start is
// implicitly "now", posts elapsed seconds to /api/popup/timestamp every
// second so a pop-up fires automatically when its timestamp is reached,
// without the user manually typing timestamps.

let _autoAdvanceInterval = null;
let _autoAdvanceStart = 0;

function toggleAutoAdvance() {
  const on = document.getElementById('popup-auto-advance').checked;
  if (_autoAdvanceInterval) {
    clearInterval(_autoAdvanceInterval);
    _autoAdvanceInterval = null;
  }
  if (on) {
    _autoAdvanceStart = Date.now();
    _autoAdvanceInterval = setInterval(async () => {
      const elapsed = Math.floor((Date.now() - _autoAdvanceStart) / 1000);
      try {
        await fetch('/api/popup/timestamp', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({timestamp: String(elapsed)}),
        });
        loadPopupState();
      } catch(e) { /* keep ticking even if one request drops */ }
    }, 1000);
  }
}
