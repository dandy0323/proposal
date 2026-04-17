const PHASE_LABELS = {
  planning: '企画・検討',
  factcheck: 'ファクトチェック',
  proposal_outline: '提案書骨子',
  mockup: 'モック作成',
  done: '完了',
};

const PHASE_COLORS = {
  planning: 'bg-blue-100 text-blue-700',
  factcheck: 'bg-yellow-100 text-yellow-700',
  proposal_outline: 'bg-purple-100 text-purple-700',
  mockup: 'bg-green-100 text-green-700',
  done: 'bg-gray-100 text-gray-700',
};

async function loadProjects() {
  const res = await fetch('/api/projects');
  const projects = await res.json();
  const container = document.getElementById('project-list');

  if (!projects.length) {
    container.innerHTML = '<div class="text-gray-400 text-sm py-8 text-center">プロジェクトがありません。「新規プロジェクト」から作成してください。</div>';
    return;
  }

  container.innerHTML = projects.map(p => {
    const phase = p.current_phase;
    const badge = `<span class="phase-badge ${PHASE_COLORS[phase] || 'bg-gray-100 text-gray-700'}">${PHASE_LABELS[phase] || phase}</span>`;
    const date = new Date(p.updated_at).toLocaleDateString('ja-JP');
    return `
      <a href="/project/${p.id}" class="block bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md p-5 transition cursor-pointer">
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
      </a>`;
  }).join('');
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
