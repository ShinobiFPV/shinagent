// Forza open world module -- shows drift/air/location state on the
// Telemetry tab's ow-panel while Forza is live and driving_mode is
// 'freeroam'. Same wrap-updateTelemetry pattern as race_engineer.js.

(function() {
  const origUpdateTelemetry = window.updateTelemetry;

  window.updateTelemetry = function(t) {
    origUpdateTelemetry(t);
    updateOpenWorld(t);
  };

  function updateOpenWorld(t) {
    const panel = document.getElementById('ow-panel');
    if (!panel) return;

    const isFreeroam = !!(t && t.source === 'forza' && t.driving_mode === 'freeroam');
    panel.style.display = isFreeroam ? 'block' : 'none';
    if (!isFreeroam) return;

    const set = (id, val, cls) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = val;
      el.className = 'data-value' + (cls ? ' ' + cls : '');
    };

    set('ow-mode', 'FREE ROAM');
    set('ow-drifting', t.drifting ? 'YES' : 'no', t.drifting ? 'good' : '');
    set('ow-angle', t.drift_angle != null ? Math.round(t.drift_angle) + '°' : '--');
    set('ow-yaw', t.peak_yaw_dps != null ? Math.round(t.peak_yaw_dps) + '°/s' : '--');
    set('ow-air', t.airborne ? 'AIRBORNE' : 'no', t.airborne ? 'warn' : '');
    set('ow-location', t.location || '--');
  }
})();
