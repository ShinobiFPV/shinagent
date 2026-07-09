// Race Engineer module -- extends the generic Telemetry tab with AC-only
// fields the shared hud.js panel doesn't already show (pit status). Forza
// telemetry has no equivalent field, so this only ever shows up when
// face/server.py's _telemetry_status() source is "ac".

(function() {
  const origUpdateTelemetry = window.updateTelemetry;

  window.updateTelemetry = function(t) {
    origUpdateTelemetry(t);
    updatePitStatus(t);
  };

  function updatePitStatus(t) {
    let pitEl = document.getElementById('re-pit-status');
    const lapPanel = document.getElementById('t-lap')?.closest('.panel');
    if (!pitEl && lapPanel) {
      pitEl = document.createElement('div');
      pitEl.id = 're-pit-status';
      pitEl.className = 'data-row';
      pitEl.innerHTML = '<span class="data-label">Pit</span><span class="data-value" id="re-pit-value">--</span>';
      lapPanel.appendChild(pitEl);
    }
    const valueEl = document.getElementById('re-pit-value');
    if (!valueEl) return;

    if (!t || t.source !== 'ac') {
      valueEl.textContent = '--';
      valueEl.className = 'data-value';
      return;
    }
    valueEl.textContent = t.is_in_pit ? 'IN PIT' : 'ON TRACK';
    valueEl.className = 'data-value ' + (t.is_in_pit ? 'warn' : 'good');
  }
})();
