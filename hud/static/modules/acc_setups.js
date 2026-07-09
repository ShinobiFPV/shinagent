// ACC Setups module -- adds delete capability on top of the core
// list/apply/generate logic already in hud.js. Q2's companion app
// (windows/acc_setup_manager.py) already supports DELETE /setups/<id>;
// hud_server.py's /api/acc/delete/<id> proxies to it.

async function deleteSetup(id) {
  if (!confirm('Delete this saved setup?')) return;
  try {
    const r = await fetch(`/api/acc/delete/${id}`, {method: 'POST'}).then(r => r.json());
    if (r.ok) {
      showToast('Setup deleted');
      loadACCSetups();
    } else {
      showToast('Delete failed: ' + (r.error || 'unknown'), 'error');
    }
  } catch(e) {
    showToast('Could not reach Q2', 'error');
  }
}
