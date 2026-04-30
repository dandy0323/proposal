const PHASE_LABELS = {
  planning: '企画・検討',
  proposal_outline: '提案書骨子',
  mockup: 'モック作成',
  done: '完了',
};

const PHASE_COLORS = {
  planning: 'bg-blue-100 text-blue-700',
  proposal_outline: 'bg-purple-100 text-purple-700',
  mockup: 'bg-green-100 text-green-700',
  done: 'bg-gray-100 text-gray-700',
};

let projectList = [];

async function loadProjects() {
  const res = await fetch('/api/projects');
  projectList = await res.json();
  renderList();
}

function renderList() {
  const container = document.getElementById('project-list');

  if (!projectList.length) {
    container.innerHTML = '<div class="text-gray-400 text-sm py-8 text-center">プロジェクトがありません。「新規プロジェクト」から作成してください。</div>';
    return;
  }

  container.innerHTML = projectList.map((p, idx) => {
    const phase = p.current_phase;
    const badge = `<span class="phase-badge ${PHASE_COLORS[phase] || 'bg-gray-100 text-gray-700'}">${PHASE_LABELS[phase] || phase}</span>`;
    const date = new Date(p.updated_at).toLocaleDateString('ja-JP');
    const isFirst = idx === 0;
    const isLast = idx === projectList.length - 1;

    return `
      <div class="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition flex items-stretch">
        <!-- Reorder buttons -->
        <div class="flex flex-col border-r border-gray-100 px-1 py-2 gap-1 justify-center">
          <button onclick="moveProject(${p.id}, 'up')" ${isFirst ? 'disabled' : ''}
            class="p-1 rounded text-gray-400 ${isFirst ? 'opacity-30 cursor-not-allowed' : 'hover:bg-gray-100 hover:text-gray-600'} transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>
          </button>
          <button onclick="moveProject(${p.id}, 'down')" ${isLast ? 'disabled' : ''}
            class="p-1 rounded text-gray-400 ${isLast ? 'opacity-30 cursor-not-allowed' : 'hover:bg-gray-100 hover:text-gray-600'} transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
          </button>
        </div>
        <!-- Project info (clickable) -->
        <a href="/project/${p.id}" class="flex-1 p-5 block">
          <div class="flex items-start justify-between">
            <div>
              <div class="font-semibold text-gray-800 mb-1">${escHtml(p.name)}</div>
              <div class="text-xs text-gray-500">${escHtml(p.form_data.industry || '')} / ${escHtml(p.form_data.system_type || '')}</div>
            </div>
            <div class="flex flex-col items-end gap-1">
              ${badge}
              <span class="text-xs text-gray-400">${date}</span>
            </div>
          </div>
        </a>
        <!-- Delete button -->
        <div class="flex items-center border-l border-gray-100 px-3">
          <button onclick="deleteProject(${p.id}, '${escHtml(p.name)}')"
            class="p-2 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </div>
      </div>`;
  }).join('');
}

async function deleteProject(id, name) {
  if (!confirm(`「${name}」を削除しますか？\nこの操作は取り消せません。`)) return;
  const res = await fetch(`/api/projects/${id}`, { method: 'DELETE' });
  if (res.ok) {
    projectList = projectList.filter(p => p.id !== id);
    renderList();
  } else {
    alert('削除に失敗しました');
  }
}

async function moveProject(id, direction) {
  const res = await fetch(`/api/projects/${id}/move?direction=${direction}`, { method: 'POST' });
  if (res.ok) {
    await loadProjects();
  }
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showForm() {
  document.getElementById('section-list').classList.add('hidden');
  document.getElementById('section-form').classList.remove('hidden');
}

function showList() {
  document.getElementById('section-form').classList.add('hidden');
  document.getElementById('section-list').classList.remove('hidden');
  loadProjects();
}

document.getElementById('btn-new').addEventListener('click', showForm);
document.getElementById('btn-cancel').addEventListener('click', showList);
document.getElementById('btn-cancel2').addEventListener('click', showList);

document.getElementById('new-project-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = Object.fromEntries(fd.entries());
  const res = await fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (res.ok) {
    const { project_id } = await res.json();
    window.location.href = `/project/${project_id}`;
  } else {
    alert('作成に失敗しました');
  }
});

loadProjects();
