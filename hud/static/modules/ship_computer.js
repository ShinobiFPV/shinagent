// Ship Computer module -- adds galaxy search (INARA/EDSM, routed through
// tools/ship_computer.py's search_galaxy) on top of the core paste/status
// logic already in hud.js. search_galaxy() returns pre-formatted natural
// language text (not structured JSON), so results render as plain text.

async function searchGalaxy() {
  const input = document.getElementById('ed-search-input');
  const query = input.value.trim();
  if (!query) return;

  const results = document.getElementById('ed-search-results');
  results.innerHTML = '<div style="color:rgba(0,200,120,0.4);font-size:11px">Searching...</div>';

  try {
    const r = await fetch('/api/ed/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query})
    }).then(r => r.json());

    if (r.ok && r.result) {
      results.innerHTML = `<div class="search-result" style="white-space:pre-wrap">${escapeHtml(r.result)}</div>`;
    } else {
      results.innerHTML = `<div style="color:#ff3c3c;font-size:11px">${escapeHtml(r.error || 'No results')}</div>`;
    }
  } catch(e) {
    results.innerHTML = '<div style="color:#ff3c3c;font-size:11px">Could not reach Q2</div>';
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
