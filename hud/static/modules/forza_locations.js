// FH6 Location Browser -- shows the Locations panel on the Telemetry tab
// while Forza is live and driving_mode is 'freeroam'. Same wrap-
// updateTelemetry pattern as forza_openworld.js, kept as a separate
// module rather than editing that file directly.

(function() {
  const origUpdateTelemetry = window.updateTelemetry;

  window.updateTelemetry = function(t) {
    origUpdateTelemetry(t);
    updateLocationsPanelVisibility(t);
  };

  function updateLocationsPanelVisibility(t) {
    const panel = document.getElementById('fh6-locations-panel');
    if (!panel) return;

    const isFreeroam = !!(t && t.source === 'forza' && t.driving_mode === 'freeroam');
    panel.style.display = isFreeroam ? 'block' : 'none';
    if (isFreeroam && !locationsInited) {
      locationsInited = true;
      loadLocationSummary();
      loadLocations();
    }
  }
})();

let locationsInited = false;
let locationData = [];
let locationSummary = {};
let locationFilter = { source: '', region: '', type: '', q: '' };

async function loadLocationSummary() {
    try {
        const r = await fetch(`${BASE}/api/forza/locations/summary`).then(r => r.json());
        locationSummary = r;
        renderLocationSummary(r);
        renderRegionFilters(r);
    } catch (e) {}
}

function renderLocationSummary(summary) {
    const el = document.getElementById('fh6-loc-summary');
    if (!el) return;

    const sources = summary.by_source || {};
    el.innerHTML = `
        <div style="letter-spacing:0.08em;margin-bottom:4px">${summary.total || 0} LANDMARKS LOADED</div>
        ${Object.entries(sources).map(([src, count]) => `
            <div style="display:flex;justify-content:space-between;padding:2px 0;
                 border-bottom:1px solid rgba(0,200,80,0.05)">
                <span style="color:rgba(0,200,80,0.6)">${src}</span>
                <span style="color:#c8f0dc">${count}</span>
            </div>
        `).join('')}
    `;
}

function renderRegionFilters(summary) {
    const el = document.getElementById('fh6-loc-region-filters');
    if (!el) return;

    const regions = summary.regions || [];
    el.innerHTML = [
        `<button class="fh6-region-btn active" data-region=""
                  style="${_fh6BtnStyle(true)}" onclick="filterByRegion('', this)">ALL</button>`,
        ...regions.map(r => `
            <button class="fh6-region-btn" data-region="${r}"
                    style="${_fh6BtnStyle(false)}" onclick="filterByRegion('${r}', this)">
                ${r.toUpperCase()}
            </button>
        `),
    ].join('');
}

function _fh6BtnStyle(active) {
    return `padding:3px 8px;font-size:9px;cursor:pointer;border-radius:3px;
            font-family:inherit;
            background:${active ? 'rgba(0,220,120,0.15)' : 'rgba(0,220,120,0.04)'};
            border:1px solid rgba(0,220,120,${active ? '0.5' : '0.15'});
            color:${active ? '#00dc78' : 'rgba(0,200,80,0.6)'}`;
}

async function loadLocations() {
    const params = new URLSearchParams(locationFilter);
    try {
        const r = await fetch(`${BASE}/api/forza/locations?${params}`).then(r => r.json());
        locationData = r.landmarks || [];
        renderLocationList();
    } catch (e) {}
}

function renderLocationList() {
    const el = document.getElementById('fh6-loc-list');
    if (!el) return;

    const SOURCE_COLORS = { builtin: 'rgba(0,200,80,0.5)', personal: '#ffb400' };

    if (!locationData.length) {
        el.innerHTML = `<div style="color:rgba(0,200,80,0.2);font-size:10px;padding:8px 0">No landmarks found</div>`;
        return;
    }

    el.innerHTML = locationData.slice(0, 50).map((lm, i) => `
        <div style="padding:5px 8px;margin-bottom:3px;background:rgba(0,200,80,0.02);
             border:1px solid rgba(0,200,80,0.08);border-radius:3px;cursor:pointer"
             onclick="toggleLocationDetail(${i})">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div style="font-size:10px;font-weight:700;color:#c8f0dc">${lm.name}</div>
                <div style="font-size:8px;color:${SOURCE_COLORS[lm.source] || 'rgba(0,200,80,0.4)'}">${lm.source || ''}</div>
            </div>
            <div style="font-size:9px;color:rgba(0,200,80,0.4);margin-top:1px">
                ${lm.region || ''}${lm.distance !== undefined ? ` &middot; ${Math.round(lm.distance)}m away` : ''}
            </div>
            <div id="fh6-loc-detail-${i}" style="display:none;font-size:9px;
                 color:rgba(0,200,80,0.6);margin-top:4px;padding-top:4px;
                 border-top:1px solid rgba(0,200,80,0.08)">
                ${(lm.notes || 'No notes recorded.').replace(/</g, '&lt;')}
            </div>
        </div>
    `).join('');
}

function toggleLocationDetail(i) {
    const el = document.getElementById(`fh6-loc-detail-${i}`);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function loadNearbyLocations() {
    try {
        const r = await fetch(`${BASE}/api/forza/locations/nearby`).then(r => r.json());
        locationData = r.nearby || [];
        renderLocationList();
        if (typeof showToast === 'function') {
            showToast(locationData.length ? `${locationData.length} landmarks nearby` : 'Nothing nearby -- not driving Forza right now?');
        }
    } catch (e) {}
}

async function importLocationMap() {
    const path = prompt("Path to .json map file:\n(or type 'reload' to refresh all sources)");
    if (!path) return;

    const r = await fetch(`${BASE}/api/forza/locations/import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: path }),
    }).then(r => r.json());

    if (typeof showToast === 'function') showToast(r.result || 'Done');
    await loadLocationSummary();
    await loadLocations();
}

async function exportPersonalMap() {
    const name = prompt("Export name (no extension):", "my_fh6_map");
    if (!name) return;

    const r = await fetch(`${BASE}/api/forza/locations/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    }).then(r => r.json());

    if (typeof showToast === 'function') showToast(r.result || 'Exported');
}

function filterByRegion(region, btn) {
    locationFilter.region = region;
    document.querySelectorAll('.fh6-region-btn').forEach(b => {
        const active = b === btn;
        b.classList.toggle('active', active);
        b.style.cssText = _fh6BtnStyle(active);
    });
    loadLocations();
}

function searchLocations(q) {
    locationFilter.q = q;
    loadLocations();
}
