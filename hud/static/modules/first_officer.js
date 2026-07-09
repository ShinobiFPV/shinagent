// First Officer module -- renders raw MSFS flight telemetry (altitude,
// airspeed, heading, autopilot, fuel) into the Telemetry tab's #fo-panel,
// shown only while the active profile is First Officer. Racing telemetry
// (speed/gear/tyres) doesn't apply to flight, so this is a separate panel
// rather than trying to force flight data into the racing field set.

async function loadFirstOfficerTelemetry() {
  const fields = document.getElementById('fo-fields');
  if (!fields) return;

  try {
    const data = await fetch('/api/telemetry/msfs').then(r => r.json());
    if (!data.active || !data.state) {
      fields.innerHTML = '<div style="color:rgba(0,200,120,0.3);font-size:11px">No flight data -- MSFS bridge not connected.</div>';
      return;
    }

    const s = data.state;
    const fuelPct = (s.fuel_qty_gal != null && s.fuel_capacity_gal)
      ? (s.fuel_qty_gal / s.fuel_capacity_gal) * 100 : null;
    const rows = [
      ['Altitude', s.altitude_ft != null ? Math.round(s.altitude_ft) + ' ft' : '--'],
      ['Airspeed', s.airspeed_ind_kt != null ? Math.round(s.airspeed_ind_kt) + ' kts' : '--'],
      ['Heading', s.heading_deg != null ? Math.round(s.heading_deg) + '°' : '--'],
      ['Autopilot', s.ap_master ? 'ON' : 'OFF'],
      ['Fuel', fuelPct != null ? Math.round(fuelPct) + '%' : '--'],
    ];

    fields.innerHTML = rows.map(([label, val]) => `
      <div style="text-align:center">
        <div style="font-size:9px;color:rgba(0,200,120,0.4);text-transform:uppercase">${label}</div>
        <div style="font-size:14px;font-weight:700;color:#c8f0dc">${val}</div>
      </div>
    `).join('');
  } catch(e) {
    fields.innerHTML = '<div style="color:#ff3c3c;font-size:11px">Could not reach Q2</div>';
  }
}
