const PHASES = ['planning', 'factcheck', 'proposal_outline', 'mockup', 'done'];
const PHASE_LABELS = {
  planning: '企画・検討',
  factcheck: 'ファクトチェック',
  proposal_outline: '提案書骨子',
  mockup: 'モック作成',
  done: '完了',
};
const RUN_LABELS = {
  planning: '企画・検討を実行',
  factcheck: 'ファクトチェックを実行',
  proposal_outline: '提案書骨子を作成',
  mockup: 'モックを作成',
};
const STATUS_LABELS = {
  pending: '確認待ち',
  approved: '承認済み',
  rejected: '差し戻し',
  edit_requested: '修正依頼中',
  superseded: '更新済み',
};

const projectId = parseInt(location.pathname.split('/').pop());
let project = null;
let currentOutput = null;

async function init() {
  project = await (await fetch(`/api/projects/${projectId}`)).json();
  document.getElementById('project-name').textContent = project.name;
  renderStepper();
  await renderPhasePanel();
}

function renderStepper() {
  const el = document.getElementById('stepper');
  el.innerHTML = PHASES.map((p, i) => {
    const active = p === project.current_phase;
    const done = PHASES.indexOf(project.current_phase) > i;
    const cls = done
      ? 'bg-green-500 text-white'
      : active
      ? 'bg-blue-600 text-white ring-2 ring-blue-300'
      : 'bg-gray-200 text-gray-500';
    const sep = i < PHASES.length - 1 ? '<div class="w-6 h-px bg-gray-300 flex-shrink-0"></div>' : '';
    return `<div class="flex items-center gap-2 flex-shrink-0">
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold ${cls}">
        ${done ? '✓ ' : ''}${PHASE_LABELS[p]}
      </div>
      ${sep}
    </div>`;
  }).join('');

  const badge = document.getElementById('phase-badge');
  badge.textContent = PHASE_LABELS[project.current_phase] || project.current_phase;
  badge.className = `phase-badge ${getBadgeColor(project.current_phase)}`;
}

function getBadgeColor(phase) {
  const map = {
    planning: 'bg-blue-100 text-blue-700',
    factcheck: 'bg-yellow-100 text-yellow-700',
    proposal_outline: 'bg-purple-100 text-purple-700',
    mockup: 'bg-green-100 text-green-700',
    done: 'bg-gray-100 text-gray-700',
  };
  return map[phase] || 'bg-gray-100 text-gray-700';
}

async function renderPhasePanel() {
  const phase = project.current_phase;
  if (phase === 'done') {
    document.getElementById('run-area').innerHTML = '<div class="text-green-600 font-semibold">すべてのフェーズが完了しました</div>';
    return;
  }

  // Run label
  document.getElementById('run-label').textContent = RUN_LABELS[phase] || 'エージェントを実行';

  // Show factcheck button only when current phase is planning (manual trigger)
  const fcBtn = document.getElementById('btn-factcheck');
  if (phase === 'planning') {
    fcBtn.classList.remove('hidden');
  } else {
    fcBtn.classList.add('hidden');
  }

  // Load existing output
  const res = await fetch(`/api/projects/${projectId}/outputs/${phase}`);
  currentOutput = await res.json();

  if (currentOutput && currentOutput.output_html) {
    showOutput(currentOutput, phase);
  } else {
    document.getElementById('output-area').classList.add('hidden');
  }
}

function showOutput(output, phase) {
  const area = document.getElementById('output-area');
  area.classList.remove('hidden');

  document.getElementById('output-phase-label').textContent = PHASE_LABELS[phase] + ' 出力結果';
  document.getElementById('output-status-label').textContent = STATUS_LABELS[output.status] || output.status;

  const iframe = document.getElementById('output-iframe');
  const blob = new Blob([output.output_html], { type: 'text/html' });
  iframe.src = URL.createObjectURL(blob);

  // Adjust iframe height after load
  iframe.onload = () => {
    try {
      const h = iframe.contentDocument.body.scrollHeight;
      iframe.style.height = Math.max(600, h + 32) + 'px';
    } catch (_) {}
  };

  // Show/hide review panel based on status
  const reviewPanel = document.getElementById('review-panel');
  if (output.status === 'approved') {
    reviewPanel.classList.add('hidden');
  } else {
    reviewPanel.classList.remove('hidden');
  }
}

async function runAgent(phase) {
  setRunning(true);
  try {
    const res = await fetch('/api/projects/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, phase }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert('エラー: ' + (err.detail || '不明なエラー'));
      return;
    }
    const data = await res.json();
    currentOutput = { id: data.output_id, output_html: data.html, status: 'pending' };
    showOutput(currentOutput, phase);
    document.getElementById('run-status').textContent = '完了';
  } catch (e) {
    alert('通信エラー: ' + e.message);
  } finally {
    setRunning(false);
  }
}

function setRunning(flag) {
  document.getElementById('btn-run').disabled = flag;
  document.getElementById('run-spinner').classList.toggle('hidden', !flag);
  document.getElementById('run-status').textContent = flag ? 'エージェント実行中...' : '';
}

async function submitReview(action, comment = '', editInstruction = '') {
  if (!currentOutput?.id) return;
  const res = await fetch('/api/projects/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      output_id: currentOutput.id,
      action,
      comment,
      edit_instruction: editInstruction,
    }),
  });
  if (!res.ok) { alert('レビュー送信に失敗しました'); return; }

  if (action === 'approve') {
    // Reload project to get updated phase
    project = await (await fetch(`/api/projects/${projectId}`)).json();
    renderStepper();
    currentOutput = null;
    document.getElementById('output-area').classList.add('hidden');
    await renderPhasePanel();
  } else if (action === 'edit') {
    // Re-run agent immediately with edit instruction
    await runAgent(project.current_phase);
  } else {
    // Rejected: just update status label
    document.getElementById('output-status-label').textContent = STATUS_LABELS['rejected'];
    document.getElementById('review-panel').classList.remove('hidden');
  }
}

// Event listeners
document.getElementById('btn-run').addEventListener('click', () => runAgent(project.current_phase));

document.getElementById('btn-factcheck').addEventListener('click', async () => {
  const originalPhase = project.current_phase;
  setRunning(true);
  try {
    const res = await fetch('/api/projects/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, phase: 'factcheck' }),
    });
    if (!res.ok) { alert('エラー'); return; }
    const data = await res.json();
    currentOutput = { id: data.output_id, output_html: data.html, status: 'pending' };
    showOutput(currentOutput, 'factcheck');
  } finally {
    setRunning(false);
  }
});

document.getElementById('btn-approve').addEventListener('click', () => submitReview('approve'));

// Reject toggle
document.getElementById('btn-reject-toggle').addEventListener('click', () => {
  document.getElementById('reject-form').classList.toggle('hidden');
  document.getElementById('edit-form').classList.add('hidden');
});
document.getElementById('btn-reject-cancel').addEventListener('click', () => {
  document.getElementById('reject-form').classList.add('hidden');
});
document.getElementById('btn-reject-submit').addEventListener('click', () => {
  const comment = document.getElementById('reject-comment').value.trim();
  submitReview('reject', comment);
  document.getElementById('reject-form').classList.add('hidden');
});

// Edit toggle
document.getElementById('btn-edit-toggle').addEventListener('click', () => {
  document.getElementById('edit-form').classList.toggle('hidden');
  document.getElementById('reject-form').classList.add('hidden');
});
document.getElementById('btn-edit-cancel').addEventListener('click', () => {
  document.getElementById('edit-form').classList.add('hidden');
});
document.getElementById('btn-edit-submit').addEventListener('click', () => {
  const instruction = document.getElementById('edit-instruction').value.trim();
  if (!instruction) { alert('修正内容を入力してください'); return; }
  document.getElementById('edit-form').classList.add('hidden');
  submitReview('edit', '', instruction);
});

init();
