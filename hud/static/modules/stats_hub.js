// ── Stats Hub Module ─────────────────────────────────────
// Interactive statistics panel for all sports. Data shapes match
// hud_server.py's /api/stats/<sport> route exactly -- see that route
// and hud/demo_data.py's get_demo_stats() for what each sport returns.

const SPORT_THEMES = {
  'formula_drift': { theme: 'theme-formula-drift', label: 'FD',  name: 'Formula Drift', color: '#e8002a' },
  'xgames':        { theme: 'theme-xgames',        label: 'XG',  name: 'X Games',        color: '#FFD700' },
  'f1':            { theme: 'theme-f1',            label: 'F1',  name: 'Formula 1',      color: '#e10600' },
  'ufc':           { theme: 'theme-ufc',           label: 'UFC', name: 'UFC',            color: '#c8102e' },
  'nba':           { theme: 'theme-nba',           label: 'NBA', name: 'NBA',            color: '#1d428a' },
  'nhl':           { theme: 'theme-nhl',           label: 'NHL', name: 'NHL',            color: '#0033a0' },
  'nfl':           { theme: 'theme-nfl',           label: 'NFL', name: 'NFL Football',    color: '#D50A0A' },
  'mlb':           { theme: 'theme-mlb',           label: 'MLB', name: 'MLB Baseball',    color: '#D50032' },
};

let currentSport = 'formula_drift';
let statsRefresh = null;
let statsHubInited = false;

// ── Initialise ────────────────────────────────────────────

function initStatsHub() {
  if (statsHubInited) return;  // tab can be reopened without re-binding intervals
  statsHubInited = true;
  buildSportSelector();
  switchSport(currentSport);
  statsRefresh = setInterval(refreshStats, 30000);
}

function buildSportSelector() {
  const sel = document.getElementById('stats-sport-selector');
  if (!sel) return;
  sel.innerHTML = Object.entries(SPORT_THEMES).map(([key, s]) => `
    <button class="sport-btn ${key === currentSport ? 'active' : ''}"
            data-sport="${key}"
            onclick="switchSport('${key}')"
            style="--sport-accent:${s.color}44; --sport-text:${s.color}">
      ${s.label}
    </button>
  `).join('');
}

function switchSport(sport) {
  currentSport = sport;

  document.querySelectorAll('.sport-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sport === sport);
  });

  const container = document.getElementById('stats-hub-container');
  if (container) {
    Object.values(SPORT_THEMES).forEach(s => container.classList.remove(s.theme));
    container.classList.add(SPORT_THEMES[sport].theme);
  }

  refreshStats();
}

async function refreshStats() {
  const sport = currentSport;
  try {
    const data = await fetch(`/api/stats/${sport}`).then(r => r.json());
    if (sport !== currentSport) return;  // sport switched again while this was in flight
    renderStats(sport, data);
  } catch (e) {
    renderStatsError(sport);
  }
}

// ── Renderers ─────────────────────────────────────────────

function renderStats(sport, data) {
  const content = document.getElementById('stats-content-area');
  if (!content) return;
  switch (sport) {
    case 'formula_drift': renderFormulaDrift(content, data); break;
    case 'xgames':        renderXGames(content, data); break;
    case 'f1':             renderF1(content, data); break;
    case 'ufc':            renderUFC(content, data); break;
    case 'nba':            renderNBA(content, data); break;
    case 'nhl':            renderNHL(content, data); break;
    case 'nfl':            renderNFL(content, data); break;
    case 'mlb':            renderMLB(content, data); break;
  }
}

function renderFormulaDrift(el, data) {
  const standings = data.standings || [];
  const schedule = data.schedule || [];

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">FORMULA DRIFT</div>

    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button class="xg-discipline-btn active" onclick="fdShowTab('standings', this)">Standings</button>
      <button class="xg-discipline-btn" onclick="fdShowTab('schedule', this)">Schedule</button>
    </div>

    <div id="fd-standings">
      <div class="stats-title">PRO Championship Standings</div>
      <table class="stats-table">
        <thead><tr><th>POS</th><th>DRIVER</th><th>CAR</th><th>PTS</th></tr></thead>
        <tbody>
          ${standings.map((d, i) => `
            <tr class="${i === 0 ? 'leader' : ''}">
              <td>${i < 3 ? `<span class="pos-badge pos-${i + 1}">${i + 1}</span>` : (d.position ?? i + 1)}</td>
              <td>
                <strong>${d.name || ''}</strong>
                <div style="font-size:9px;color:rgba(200,100,100,0.5)">${d.country || (d.car_number ? '#' + d.car_number : '')}</div>
              </td>
              <td style="font-size:9px;color:rgba(200,100,100,0.5)">${d.car || ''}</td>
              <td style="color:#e8002a;font-weight:700">${d.points ?? 0}</td>
            </tr>
          `).join('') || '<tr><td colspan="4" style="text-align:center;color:rgba(200,100,100,0.3)">No standings available</td></tr>'}
        </tbody>
      </table>
    </div>

    <div id="fd-schedule" style="display:none">
      <div class="stats-title">Schedule</div>
      ${schedule.map(e => `
        <div style="padding:8px;margin-bottom:6px;background:rgba(232,0,42,0.04);border:1px solid rgba(232,0,42,0.1);border-radius:4px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="font-size:11px;font-weight:700;color:#f0e0e0">Round ${e.round}: ${e.name}</div>
              <div style="font-size:9px;color:rgba(200,100,100,0.5)">${e.location || ''}</div>
            </div>
            <div style="font-size:9px;color:#e8002a">${e.date || ''}</div>
          </div>
          <div style="margin-top:4px">
            <span style="font-size:9px;padding:2px 6px;border-radius:2px;
                 background:${e.status === 'complete' ? 'rgba(0,220,120,0.1)' : 'rgba(232,0,42,0.1)'};
                 color:${e.status === 'complete' ? '#00dc78' : '#e8002a'}">
              ${(e.status || 'unknown').toUpperCase()}
            </span>
          </div>
        </div>
      `).join('') || '<div style="text-align:center;color:rgba(200,100,100,0.3);padding:20px 0">No schedule available</div>'}
    </div>
  `;
}

function fdShowTab(tab, btn) {
  document.getElementById('fd-standings').style.display = tab === 'standings' ? 'block' : 'none';
  document.getElementById('fd-schedule').style.display = tab === 'schedule' ? 'block' : 'none';
  btn.parentElement.querySelectorAll('.xg-discipline-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function renderXGames(el, data) {
  const results = data.results || [];

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">X GAMES</div>

    <div class="xg-discipline-tabs">
      <button class="xg-discipline-btn active" onclick="xgFilter('all', this)">ALL</button>
      <button class="xg-discipline-btn" onclick="xgFilter('snowboard', this)">SNOWBOARD</button>
      <button class="xg-discipline-btn" onclick="xgFilter('ski', this)">SKI</button>
      <button class="xg-discipline-btn" onclick="xgFilter('skateboard', this)">SKATE</button>
      <button class="xg-discipline-btn" onclick="xgFilter('bmx', this)">BMX</button>
      <button class="xg-discipline-btn" onclick="xgFilter('moto_x', this)">MOTO X</button>
    </div>

    <div class="stats-title">Results</div>

    <div id="xg-results-grid">
      ${results.map(r => `
        <div class="xg-result-card" data-discipline="${r.discipline || 'all'}"
             style="padding:8px;margin-bottom:6px;background:rgba(255,215,0,0.03);border:1px solid rgba(255,215,0,0.1);border-radius:4px">
          <div style="font-size:9px;color:rgba(255,215,0,0.5);text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">${r.event || ''}</div>
          <div style="display:flex;align-items:center;gap:10px">
            <span class="pos-badge pos-1" style="flex-shrink:0">G</span>
            <div style="flex:1"><div style="font-size:12px;font-weight:700;color:#e8eef8">${r.gold || 'TBD'}</div></div>
            <div class="xg-score-small">${r.score || ''}</div>
          </div>
        </div>
      `).join('') || '<div style="text-align:center;color:rgba(255,215,0,0.2);padding:20px 0">No results available</div>'}
    </div>
  `;
}

function xgFilter(discipline, btn) {
  document.querySelectorAll('.xg-result-card').forEach(c => {
    c.style.display = (discipline === 'all' || c.dataset.discipline === discipline) ? 'block' : 'none';
  });
  btn.parentElement.querySelectorAll('.xg-discipline-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function renderF1(el, data) {
  const status = data.race_status || {};
  const drivers = data.standings || [];

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">FORMULA 1</div>

    ${status.connected ? `
      <div style="padding:8px;background:rgba(225,6,0,0.05);border:1px solid rgba(225,6,0,0.15);border-radius:4px;margin-bottom:10px">
        <div style="font-size:9px;color:rgba(225,6,0,0.6);text-transform:uppercase;letter-spacing:0.1em">Live Race</div>
        <div style="font-size:12px;font-weight:700;color:#f0e8e8;margin-top:4px">${status.event_name || '--'}</div>
        <div style="font-size:11px;color:rgba(225,6,0,0.8)">${status.period_str || '--'} ${status.time ? '&bull; ' + status.time : ''}</div>
      </div>
    ` : '<div style="color:rgba(225,6,0,0.3);font-size:11px;padding:10px 0;text-align:center">No live session</div>'}

    <div class="stats-title">Driver Standings</div>
    <table class="stats-table">
      <thead><tr><th>POS</th><th>DRIVER</th><th>TEAM</th><th>PTS</th></tr></thead>
      <tbody>
        ${drivers.slice(0, 10).map((d, i) => `
          <tr>
            <td>${i < 3 ? `<span class="pos-badge pos-${i + 1}">${i + 1}</span>` : i + 1}</td>
            <td style="font-weight:700">${d.name || '--'}</td>
            <td style="font-size:9px;color:rgba(200,100,100,0.5)">${d.team || '--'}</td>
            <td style="color:#e10600;font-weight:700">${d.points || 0}</td>
          </tr>
        `).join('') || '<tr><td colspan="4" style="text-align:center;color:rgba(200,100,100,0.3)">Season standings not available yet</td></tr>'}
      </tbody>
    </table>
  `;
}

function renderUFC(el, data) {
  const event = data.event || {};
  const fight = data.current_fight || {};

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">UFC</div>

    ${event.name ? `
      <div style="padding:8px;background:rgba(200,16,46,0.05);border:1px solid rgba(200,16,46,0.15);border-radius:4px;margin-bottom:10px">
        <div style="font-size:9px;color:rgba(200,16,46,0.6);text-transform:uppercase;letter-spacing:0.1em">Event</div>
        <div style="font-size:12px;font-weight:700;color:#f0e0e0">${event.name}</div>
        <div style="font-size:10px;color:rgba(200,100,100,0.6)">${event.venue || ''} ${event.date ? '&bull; ' + event.date : ''}</div>
      </div>
    ` : ''}

    ${fight.fighter1 ? `
      <div class="stats-title">Main Event</div>
      <div style="display:flex;justify-content:space-between;align-items:center;padding:12px;background:rgba(200,16,46,0.04);border:1px solid rgba(200,16,46,0.12);border-radius:6px">
        <div style="text-align:center;flex:1">
          <div class="ufc-fighter-name">${fight.fighter1}</div>
          <div class="ufc-record">${fight.record1 || ''}</div>
        </div>
        <div style="text-align:center;padding:0 12px">
          <div style="font-size:9px;color:rgba(200,16,46,0.5);letter-spacing:0.1em">VS</div>
          <div style="font-size:9px;color:rgba(200,100,100,0.5)">${fight.weight_class || ''}</div>
        </div>
        <div style="text-align:center;flex:1">
          <div class="ufc-fighter-name">${fight.fighter2}</div>
          <div class="ufc-record">${fight.record2 || ''}</div>
        </div>
      </div>
    ` : '<div style="color:rgba(200,100,100,0.3);font-size:11px;padding:20px 0;text-align:center">No event data</div>'}
  `;
}

function renderNBA(el, data) {
  const status = data.game_status || {};

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">NBA</div>

    ${status.active ? `
      <div style="padding:16px;background:rgba(23,64,139,0.08);border:1px solid rgba(23,64,139,0.2);border-radius:8px;text-align:center;margin-bottom:10px">
        <div style="font-size:9px;color:rgba(100,140,200,0.6);letter-spacing:0.1em;margin-bottom:8px">
          ${status.period_str || '--'} ${status.time ? '&bull; ' + status.time : ''}
        </div>
        <div style="display:flex;justify-content:center;align-items:center;gap:20px">
          <div><div class="nba-team-name">${status.home_abbr || 'HOM'}</div><div class="nba-score-big">${status.home_score || 0}</div></div>
          <div style="font-size:14px;color:rgba(100,140,200,0.4)">-</div>
          <div><div class="nba-team-name">${status.away_abbr || 'AWY'}</div><div class="nba-score-big">${status.away_score || 0}</div></div>
        </div>
        <div style="margin-top:8px;font-size:10px;color:rgba(100,140,200,0.6)">
          ${status.leading && status.leading !== 'Tied' ? status.leading + ' leads by ' + status.margin : 'Tied game'}
        </div>
      </div>
    ` : '<div style="color:rgba(100,140,200,0.3);font-size:11px;padding:20px 0;text-align:center">No active game</div>'}
  `;
}

function renderNHL(el, data) {
  const status = data.game_status || {};
  const goals = data.recent_goals || [];

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">NHL</div>

    ${status.active ? `
      <div style="padding:16px;background:rgba(0,51,160,0.06);border:1px solid rgba(0,51,160,0.2);border-radius:8px;text-align:center;margin-bottom:10px">
        <div style="font-size:9px;color:rgba(100,130,200,0.6);letter-spacing:0.1em;margin-bottom:8px">
          ${status.period_str || '--'} ${status.clock ? (status.in_intermission ? '&bull; INTERMISSION' : '&bull; ' + status.clock) : ''}
        </div>
        <div style="display:flex;justify-content:center;align-items:center;gap:20px">
          <div>
            <div style="font-size:11px;font-weight:700;color:#0033a0;text-transform:uppercase;letter-spacing:0.06em">${status.home_abbr || 'HOM'}</div>
            <div class="nhl-score-big">${status.home_score || 0}</div>
          </div>
          <div style="font-size:16px;color:rgba(100,130,200,0.3)">-</div>
          <div>
            <div style="font-size:11px;font-weight:700;color:#0033a0;text-transform:uppercase;letter-spacing:0.06em">${status.away_abbr || 'AWY'}</div>
            <div class="nhl-score-big">${status.away_score || 0}</div>
          </div>
        </div>
        <div style="margin-top:8px;font-size:10px;color:rgba(100,130,200,0.6)">
          ${status.leading && status.leading !== 'Tied' ? status.leading + ' leads' : 'Tied'}
        </div>
      </div>
    ` : '<div style="color:rgba(100,130,200,0.3);font-size:11px;padding:20px 0;text-align:center">No active game</div>'}

    ${goals.length > 0 ? `
      <div class="stats-title">Recent Goals</div>
      ${goals.slice(-5).reverse().map(g => `
        <div style="padding:5px 8px;margin-bottom:4px;border-left:2px solid #0033a0;background:rgba(0,51,160,0.04)">
          <div style="font-size:11px;font-weight:700;color:#e8eef8">${g.team}</div>
          <div style="font-size:9px;color:rgba(100,130,200,0.5)">${g.period_str || ''} ${g.time ? '&bull; ' + g.time : ''}</div>
        </div>
      `).join('')}
    ` : ''}
  `;
}

function renderNFL(el, data) {
  const status = data.game_status || {};
  const drives = data.recent_drives || [];

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">NFL</div>

    ${status.active ? `
      <div style="padding:16px;background:rgba(1,51,105,0.07);border:1px solid rgba(1,51,105,0.2);border-radius:8px;text-align:center;margin-bottom:10px">
        <div style="font-size:9px;color:rgba(100,130,200,0.6);letter-spacing:0.1em;margin-bottom:8px">
          ${status.period_str || '--'}${status.clock ? ' &bull; ' + status.clock : ''}
        </div>
        <div style="display:flex;justify-content:center;align-items:center;gap:16px">
          <div>
            <div class="nfl-team-abbr">${status.home_abbr || 'HOM'}</div>
            <div class="nfl-score-big">${status.home_score ?? 0}</div>
          </div>
          <div style="font-size:16px;color:rgba(100,130,200,0.3)">-</div>
          <div>
            <div class="nfl-team-abbr">${status.away_abbr || 'AWY'}</div>
            <div class="nfl-score-big">${status.away_score ?? 0}</div>
          </div>
        </div>
        ${status.down_distance ? `
          <div class="nfl-down-dist" style="margin-top:8px">
            ${status.possession ? `<span class="nfl-possession-indicator"></span>${status.possession} &bull;` : ''}
            ${status.down_distance}
            ${status.yard_line ? ` &bull; Yd ${status.yard_line}` : ''}
          </div>
        ` : ''}
        <div style="margin-top:6px;font-size:10px;color:rgba(100,130,200,0.6)">
          ${status.leading && status.leading !== 'Tied' ? status.leading + ' leads by ' + status.margin : 'Tied game'}
        </div>
      </div>
    ` : '<div style="color:rgba(100,130,200,0.3);font-size:11px;padding:20px 0;text-align:center">No active game</div>'}

    ${drives.length ? `
      <div class="stats-title">RECENT DRIVES</div>
      ${drives.slice(-4).reverse().map(d => `
        <div style="padding:5px 8px;margin-bottom:4px;border-left:2px solid #4169e1;background:rgba(1,51,105,0.04);font-size:9px">
          <div style="color:#e8eef8;font-weight:700">${d.team || ''}</div>
          <div style="color:rgba(100,130,200,0.5)">${d.plays || 0} plays &bull; ${d.yards || 0} yds &bull; ${d.result || ''}</div>
        </div>
      `).join('')}
    ` : ''}
  `;
}

function renderMLB(el, data) {
  const status = data.game_status || {};
  // [1B, 2B, 3B] -- matches integrations/mlb_data.py's _bases_occupied()
  // order (postOnFirst/postOnSecond/postOnThird), not visual diamond
  // position order.
  const bases = status.bases || [false, false, false];

  el.innerHTML = `
    <div class="sport-logo" style="margin-bottom:12px">MLB</div>

    ${status.active ? `
      <div style="padding:16px;background:rgba(0,45,114,0.06);border:1px solid rgba(0,45,114,0.2);border-radius:8px;text-align:center;margin-bottom:10px">
        <div class="mlb-inning" style="margin-bottom:8px">${status.inning_str || '--'}</div>

        <div style="display:flex;justify-content:center;align-items:center;gap:16px">
          <div>
            <div class="mlb-team-name">${status.home_abbr || 'HOM'}</div>
            <div class="mlb-score-big">${status.home_score ?? 0}</div>
            <div style="font-size:9px;color:rgba(100,130,200,0.4)">${status.home_hits ?? 0}H</div>
          </div>

          <div style="text-align:center">
            <div class="mlb-count">${status.count || '0-0'}</div>
            <div style="font-size:8px;color:rgba(100,130,200,0.4);margin-bottom:6px">B-S</div>

            <div class="mlb-bases">
              <div class="mlb-base ${bases[1] ? 'occupied' : ''}" style="top:0;left:50%;transform:translate(-50%,-50%) rotate(45deg)"></div>
              <div class="mlb-base ${bases[0] ? 'occupied' : ''}" style="top:50%;right:0;transform:translate(50%,-50%) rotate(45deg)"></div>
              <div class="mlb-base ${bases[2] ? 'occupied' : ''}" style="top:50%;left:0;transform:translate(-50%,-50%) rotate(45deg)"></div>
            </div>

            <div class="mlb-outs">
              ${[0, 1, 2].map(i => `<div class="mlb-out ${i < (status.outs ?? 0) ? 'recorded' : ''}"></div>`).join('')}
            </div>
          </div>

          <div>
            <div class="mlb-team-name">${status.away_abbr || 'AWY'}</div>
            <div class="mlb-score-big">${status.away_score ?? 0}</div>
            <div style="font-size:9px;color:rgba(100,130,200,0.4)">${status.away_hits ?? 0}H</div>
          </div>
        </div>

        ${status.batter ? `
          <div style="margin-top:8px;font-size:10px;color:rgba(100,150,220,0.7)">
            ${status.batter}${status.pitcher ? ` vs ${status.pitcher}` : ''}
          </div>
        ` : ''}

        <div style="margin-top:6px;font-size:10px;color:rgba(100,130,200,0.5)">
          ${status.leading && status.leading !== 'Tied' ? status.leading + ' leads by ' + status.margin : 'Tied game'}
        </div>
      </div>
    ` : '<div style="color:rgba(100,130,200,0.3);font-size:11px;padding:20px 0;text-align:center">No active game</div>'}
  `;
}

function renderStatsError(sport) {
  const content = document.getElementById('stats-content-area');
  if (content) {
    content.innerHTML = `
      <div style="color:rgba(200,200,200,0.2);font-size:11px;padding:30px 0;text-align:center">
        Could not load ${SPORT_THEMES[sport]?.name || sport} data
      </div>
    `;
  }
}
