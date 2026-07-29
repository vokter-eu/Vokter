// Personal-memory review window (Phase 1). Full transparency: everything Vokter
// knows about you is listed here, and you can edit or delete any of it.
async function loadMemory() {
  const list = document.getElementById('memory-list');
  if (!list) return;
  let data;
  try { data = await (await fetch('/api/memory')).json(); } catch { return; }
  list.innerHTML = '';
  if (!data.memory || !data.memory.length) {
    list.innerHTML = '<div style="color:var(--muted);font-size:.82rem">Nothing yet — Vokter only knows what you tell it to remember.</div>';
    return;
  }
  for (const m of data.memory) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:6px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px';
    const span = document.createElement('span');
    span.textContent = m.content; span.style.cssText = 'flex:1;font-size:.85rem';
    const edit = document.createElement('button');
    edit.textContent = 'Edit'; edit.className = 'btn-sm';
    edit.onclick = async () => {
      const v = prompt('Edit what Vokter remembers:', m.content);
      if (v && v.trim()) {
        await fetch('/api/memory/' + m.id, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:v.trim()})});
        loadMemory();
      }
    };
    const del = document.createElement('button');
    del.textContent = 'Delete'; del.className = 'btn-sm danger';
    del.onclick = async () => { await fetch('/api/memory/' + m.id, {method:'DELETE'}); loadMemory(); };
    if (m.source === 'learned') {          // Phase 2c — mark facts Vokter proposed
      const badge = document.createElement('span');
      badge.className = 'mem-badge';
      badge.textContent = 'learned';
      badge.title = 'Vokter noticed this in a chat and you confirmed it';
      row.append(badge, span, edit, del);
    } else {
      row.append(span, edit, del);
    }
    list.appendChild(row);
  }
}
const _memAdd = document.getElementById('btn-memory-add');
if (_memAdd) _memAdd.onclick = async () => {
  const inp = document.getElementById('memory-add-input');
  const v = inp.value.trim(); if (!v) return;
  await fetch('/api/memory', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:v})});
  inp.value = ''; loadMemory();
};
const _memForget = document.getElementById('btn-memory-forget-all');
if (_memForget) _memForget.onclick = async () => {
  if (!confirm('Forget EVERYTHING Vokter knows about you? This deletes it permanently and cannot be undone.')) return;
  await fetch('/api/memory', {method:'DELETE'}); loadMemory();
};
loadMemory();
