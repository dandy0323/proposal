// ── Utilities ──────────────────────────────────────────────────────────────────

function loadIframe(iframe, html, minHeight) {
  // Start large so all content (including min-h-screen) renders before measuring.
  iframe.style.height = '20000px';
  iframe.onload = () => {
    try {
      const doc = iframe.contentDocument || iframe.contentWindow.document;
      // getBoundingClientRect().bottom gives actual rendered position,
      // unaffected by min-height/height:100% on body or html elements.
      let maxBottom = minHeight;
      const els = doc.body.getElementsByTagName('*');
      for (let i = 0; i < els.length; i++) {
        try {
          const b = els[i].getBoundingClientRect().bottom;
          if (b > maxBottom) maxBottom = b;
        } catch (_) {}
      }
      iframe.style.height = (maxBottom + 40) + 'px';
    } catch (_) {
      iframe.style.height = minHeight + 'px';
    }
  };
  iframe.src = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
}

// ── Constants ──────────────────────────────────────────────────────────────────

// ── Constants ──────────────────────────────────────────────────────────────────

const PHASES = ['planning', 'factcheck', 'proposal_outline', 'mockup', 'done'];
const PHASE_LABELS = {
  planning: '企画・検討',
  factcheck: 'ファクトチェック',
  proposal_outline: '提案書骨子',
  mockup: 'モック作成',
  done: '完了',
};
const RUN_LABELS = {
  factcheck: 'ファクトチェックを実行',
  proposal_outline: '提案書骨子を作成',
  mockup: 'モックを作成',
};

const SUB_PHASES = [
  'why_background', 'why_market', 'why_business_model',
  'who_persona', 'who_value', 'who_ux',
  'what_features', 'what_ia', 'what_nonfunc',
  'how_platform', 'how_feasibility', 'how_integration',
  'project_schedule', 'project_budget', 'project_legal',
];
const SUB_PHASE_LABELS = {
  why_background:    '背景と目的の明確化',
  why_market:        '市場・競合分析',
  why_business_model:'ビジネスモデル・収益化',
  who_persona:       'ペルソナ定義とユーザー理解',
  who_value:         '提供価値（バリュープロポジション）',
  who_ux:            'UX設計・カスタマージャーニー',
  what_features:     '機能洗い出しと優先順位付け',
  what_ia:           '情報設計とUIの方向性',
  what_nonfunc:      '非機能要件の方向性',
  how_platform:      'プラットフォームとアーキテクチャ',
  how_feasibility:   '技術的実現可能性',
  how_integration:   '外部連携とデータ',
  project_schedule:  'スケジュールとマイルストーン',
  project_budget:    '予算と体制',
  project_legal:     '法務・コンプライアンス',
};
const SUB_PHASE_GROUPS = [
  { label: 'ビジネス・戦略（Why）', keys: ['why_background', 'why_market', 'why_business_model'] },
  { label: 'ユーザー・体験（Who）', keys: ['who_persona', 'who_value', 'who_ux'] },
  { label: 'プロダクト・機能（What）', keys: ['what_features', 'what_ia', 'what_nonfunc'] },
  { label: 'システム・技術（How）', keys: ['how_platform', 'how_feasibility', 'how_integration'] },
  { label: '計画・制約（Project）', keys: ['project_schedule', 'project_budget', 'project_legal'] },
];
const STATUS_LABELS = {
  pending: '確認待ち',
  approved: '承認済み',
  rejected: '差し戻し',
  edit_requested: '修正依頼中',
  superseded: '更新済み',
};

// ── State ──────────────────────────────────────────────────────────────────────

const projectId = parseInt(location.pathname.split('/').pop());
let project = null;
let subPhaseOutputs = {};   // key -> output object (for planning phase)
let selectedSubPhase = null; // currently displayed sub-phase key
let currentOutput = null;    // for non-planning phase panel

// ── Init ───────────────────────────────────────────────────────────────────────

async function init() {
  project = await (await fetch(`/api/projects/${projectId}`)).json();
  document.getElementById('project-name').textContent = project.name;
  renderStepper();

  if (project.current_phase === 'planning') {
    await renderSubPhasePanel();
  } else {
    await renderPhasePanel();
  }
}

// ── Stepper ────────────────────────────────────────────────────────────────────

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
      <div class="px-3 py-1.5 rounded-full text-xs font-semibold ${cls}">${done ? '✓ ' : ''}${PHASE_LABELS[p]}</div>
      ${sep}
    </div>`;
  }).join('');

  const badge = document.getElementById('phase-badge');
  badge.textContent = PHASE_LABELS[project.current_phase] || project.current_phase;
  badge.className = 'phase-badge ' + getBadgeColor(project.current_phase);
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

// ── Sub-phase panel (planning) ─────────────────────────────────────────────────

async function renderSubPhasePanel() {
  document.getElementById('sub-phase-panel').classList.remove('hidden');
  document.getElementById('phase-panel').classList.add('hidden');

  const res = await fetch(`/api/projects/${projectId}/sub-phases`);
  subPhaseOutputs = await res.json();

  renderSubSidebar();
  selectSubPhase(project.current_sub_phase || SUB_PHASES[0]);
}

function renderSubSidebar() {
  const sidebar = document.getElementById('sub-sidebar');
  const currentIdx = SUB_PHASES.indexOf(project.current_sub_phase);

  sidebar.innerHTML = SUB_PHASE_GROUPS.map(group => {
    const items = group.keys.map(key => {
      const output = subPhaseOutputs[key];
      const keyIdx = SUB_PHASES.indexOf(key);
      const isCurrent = key === project.current_sub_phase;
      const isSelected = key === selectedSubPhase;
      const isFuture = keyIdx > currentIdx;
      const isClickable = !isFuture || !!output;

      // Status icon
      let icon = '○';
      let iconCls = isFuture ? 'text-gray-300' : 'text-gray-400';
      if (output) {
        switch (output.status) {
          case 'approved':      icon = '✓'; iconCls = 'text-green-500'; break;
          case 'pending':       icon = '●'; iconCls = 'text-blue-500';  break;
          case 'rejected':      icon = '✗'; iconCls = 'text-red-500';   break;
          case 'edit_requested':icon = '↺'; iconCls = 'text-orange-500';break;
        }
      } else if (isCurrent) {
        icon = '→'; iconCls = 'text-blue-400';
      }

      const bgCls = isSelected
        ? 'bg-blue-50 border-l-2 border-blue-500'
        : isCurrent && !isSelected
        ? 'bg-yellow-50 border-l-2 border-yellow-400'
        : isClickable ? 'hover:bg-gray-100' : '';
      const textCls = isFuture && !output ? 'text-gray-400' : 'text-gray-700';
      const cursor = isClickable ? 'cursor-pointer' : 'cursor-default';

      const onclick = isClickable ? `onclick="selectSubPhase('${key}')"` : '';
      return `<div class="px-3 py-2 flex items-start gap-2 ${bgCls} ${cursor} transition" ${onclick}>
        <span class="text-xs ${iconCls} w-4 text-center flex-shrink-0 mt-0.5">${icon}</span>
        <span class="text-xs ${textCls} leading-snug">${SUB_PHASE_LABELS[key]}</span>
      </div>`;
    }).join('');

    return `<div class="mb-1">
      <div class="px-3 py-1.5 text-xs font-bold text-gray-400 uppercase tracking-wide bg-gray-100">${group.label}</div>
      ${items}
    </div>`;
  }).join('');
}

function selectSubPhase(key) {
  selectedSubPhase = key;
  const output = subPhaseOutputs[key] || null;
  const currentIdx = SUB_PHASES.indexOf(project.current_sub_phase);
  const keyIdx = SUB_PHASES.indexOf(key);
  const isCurrentActive = key === project.current_sub_phase;
  const isFuture = keyIdx > currentIdx && !output;

  // Header
  document.getElementById('sub-phase-title').textContent = SUB_PHASE_LABELS[key] || key;
  const statusText = output ? (STATUS_LABELS[output.status] || output.status) : (isFuture ? '未開始' : '未実行');
  document.getElementById('sub-status-label').textContent = statusText;

  // Run button: show only if this is the current active sub-phase
  const runArea = document.getElementById('sub-run-area');
  const btnRun = document.getElementById('btn-sub-run');
  if (isCurrentActive) {
    runArea.classList.remove('hidden');
    if (output && output.status === 'edit_requested') {
      btnRun.textContent = '修正を再実行';
    } else if (output && output.status === 'pending') {
      btnRun.textContent = '再実行';
    } else if (output && output.status === 'rejected') {
      btnRun.textContent = 'エージェントを再実行';
    } else {
      btnRun.textContent = 'エージェントを実行';
    }
  } else {
    runArea.classList.add('hidden');
  }

  // Reset spinner/status
  document.getElementById('sub-spinner').classList.add('hidden');
  document.getElementById('sub-run-status').textContent = '';

  // iframe
  const iframe = document.getElementById('sub-iframe');
  const emptyState = document.getElementById('sub-empty-state');
  if (output && output.output_html) {
    emptyState.classList.add('hidden');
    iframe.classList.remove('hidden');
    loadIframe(iframe, output.output_html, 500);
  } else {
    iframe.classList.add('hidden');
    iframe.src = 'about:blank';
    if (!isCurrentActive) {
      emptyState.classList.remove('hidden');
    } else {
      emptyState.classList.add('hidden');
    }
  }

  // Review panel: show only for pending output on current active sub-phase
  const reviewPanel = document.getElementById('sub-review-panel');
  if (output && output.status === 'pending' && isCurrentActive) {
    reviewPanel.classList.remove('hidden');
  } else {
    reviewPanel.classList.add('hidden');
  }
  document.getElementById('sub-reject-form').classList.add('hidden');
  document.getElementById('sub-edit-form').classList.add('hidden');
  document.getElementById('sub-truncation-banner').classList.add('hidden');

  // Deep-dive panel: why_market only, when pending, on current active sub-phase
  const deepDivePanel = document.getElementById('deep-dive-panel');
  if (key === 'why_market' && output && output.status === 'pending' && isCurrentActive) {
    deepDivePanel.classList.remove('hidden');
  } else {
    deepDivePanel.classList.add('hidden');
  }

  renderSubSidebar();
}

// ── Sub-phase run ──────────────────────────────────────────────────────────────

async function runSubPhase(key, continueMode = false) {
  setSubRunning(true);
  if (continueMode) {
    document.getElementById('sub-run-status').textContent = '続きを生成中...';
  }
  try {
    const res = await fetch('/api/projects/run-sub-phase', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, sub_phase_key: key, continue_mode: continueMode }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert('エラー: ' + (err.detail || '不明なエラー'));
      return;
    }
    const data = await res.json();
    subPhaseOutputs[key] = { id: data.output_id, output_html: data.html, status: 'pending', sub_phase_key: key };
    document.getElementById('sub-run-status').textContent = '完了';
    selectSubPhase(key);
    if (data.truncated) {
      document.getElementById('sub-truncation-banner').classList.remove('hidden');
      document.getElementById('sub-review-panel').classList.add('hidden');
    }
  } catch (e) {
    alert('通信エラー: ' + e.message);
  } finally {
    setSubRunning(false);
  }
}

function setSubRunning(flag) {
  document.getElementById('btn-sub-run').disabled = flag;
  document.getElementById('sub-spinner').classList.toggle('hidden', !flag);
  if (flag) document.getElementById('sub-run-status').textContent = 'エージェント実行中...';
}

// ── Sub-phase review ───────────────────────────────────────────────────────────

async function reviewSubPhase(action, comment = '', editInstruction = '') {
  const output = subPhaseOutputs[selectedSubPhase];
  if (!output || !output.id) { alert('レビュー対象の出力がありません'); return; }

  const res = await fetch('/api/projects/sub-phase-review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ output_id: output.id, action, comment, edit_instruction: editInstruction }),
  });
  if (!res.ok) { alert('レビュー送信に失敗しました'); return; }
  const data = await res.json();

  if (action === 'approve') {
    subPhaseOutputs[selectedSubPhase] = { ...output, status: 'approved' };
    // Reload project to get updated current_sub_phase / current_phase
    project = await (await fetch(`/api/projects/${projectId}`)).json();
    renderStepper();

    if (project.current_phase !== 'planning') {
      // All sub-phases done → switch to regular phase panel
      document.getElementById('sub-phase-panel').classList.add('hidden');
      document.getElementById('phase-panel').classList.remove('hidden');
      await renderPhasePanel();
    } else {
      // Advance to next sub-phase
      const nextKey = data.next_sub_phase;
      const nextTarget = (nextKey && nextKey !== 'done') ? nextKey : project.current_sub_phase;
      renderSubSidebar();
      selectSubPhase(nextTarget);
    }
  } else if (action === 'edit') {
    subPhaseOutputs[selectedSubPhase] = { ...output, status: 'edit_requested', edit_instruction: editInstruction };
    await runSubPhase(selectedSubPhase);
  } else {
    subPhaseOutputs[selectedSubPhase] = { ...output, status: 'rejected', review_comment: comment };
    document.getElementById('sub-status-label').textContent = STATUS_LABELS['rejected'];
    renderSubSidebar();
  }
}

// ── Deep-dive ──────────────────────────────────────────────────────────────────

async function runDeepDive() {
  const input = document.getElementById('deep-dive-input').value.trim();
  if (!input) { alert('追加で調査したい内容を入力してください'); return; }

  setDeepDiveRunning(true);
  try {
    const res = await fetch('/api/projects/deep-dive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, deep_dive_request: input }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert('エラー: ' + (err.detail || '不明なエラー'));
      return;
    }
    const data = await res.json();
    subPhaseOutputs['why_market'] = { id: data.output_id, output_html: data.html, status: 'pending', sub_phase_key: 'why_market' };
    document.getElementById('deep-dive-input').value = '';
    selectSubPhase('why_market');
  } catch (e) {
    alert('通信エラー: ' + e.message);
  } finally {
    setDeepDiveRunning(false);
  }
}

function setDeepDiveRunning(flag) {
  document.getElementById('btn-deep-dive').disabled = flag;
  document.getElementById('deep-dive-spinner').classList.toggle('hidden', !flag);
}

// ── Regular phase panel (factcheck / proposal_outline / mockup) ────────────────

async function renderPhasePanel() {
  const phase = project.current_phase;
  if (phase === 'done') {
    document.getElementById('run-area').innerHTML =
      '<div class="text-green-600 font-semibold text-sm">🎉 すべてのフェーズが完了しました</div>';
    document.getElementById('output-area').classList.add('hidden');
    return;
  }

  document.getElementById('run-label').textContent = RUN_LABELS[phase] || 'エージェントを実行';

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
  loadIframe(iframe, output.output_html, 600);

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
  if (!currentOutput || !currentOutput.id) return;
  const res = await fetch('/api/projects/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ output_id: currentOutput.id, action, comment, edit_instruction: editInstruction }),
  });
  if (!res.ok) { alert('レビュー送信に失敗しました'); return; }

  if (action === 'approve') {
    project = await (await fetch(`/api/projects/${projectId}`)).json();
    renderStepper();
    currentOutput = null;
    document.getElementById('output-area').classList.add('hidden');
    await renderPhasePanel();
  } else if (action === 'edit') {
    await runAgent(project.current_phase);
  } else {
    document.getElementById('output-status-label').textContent = STATUS_LABELS['rejected'];
    document.getElementById('review-panel').classList.remove('hidden');
  }
}

// ── Event listeners ────────────────────────────────────────────────────────────

// Sub-phase controls
document.getElementById('btn-sub-run').addEventListener('click', () => runSubPhase(selectedSubPhase));

document.getElementById('btn-truncation-proceed').addEventListener('click', () => {
  document.getElementById('sub-truncation-banner').classList.add('hidden');
  document.getElementById('sub-review-panel').classList.remove('hidden');
});
document.getElementById('btn-truncation-continue').addEventListener('click', () => {
  document.getElementById('sub-truncation-banner').classList.add('hidden');
  runSubPhase(selectedSubPhase, true); // continue_mode = true
});
document.getElementById('btn-sub-approve').addEventListener('click', () => reviewSubPhase('approve'));

document.getElementById('btn-sub-reject-toggle').addEventListener('click', () => {
  document.getElementById('sub-reject-form').classList.toggle('hidden');
  document.getElementById('sub-edit-form').classList.add('hidden');
});
document.getElementById('btn-sub-reject-cancel').addEventListener('click', () => {
  document.getElementById('sub-reject-form').classList.add('hidden');
});
document.getElementById('btn-sub-reject-submit').addEventListener('click', () => {
  const comment = document.getElementById('sub-reject-comment').value.trim();
  reviewSubPhase('reject', comment);
  document.getElementById('sub-reject-form').classList.add('hidden');
});

document.getElementById('btn-sub-edit-toggle').addEventListener('click', () => {
  document.getElementById('sub-edit-form').classList.toggle('hidden');
  document.getElementById('sub-reject-form').classList.add('hidden');
});
document.getElementById('btn-sub-edit-cancel').addEventListener('click', () => {
  document.getElementById('sub-edit-form').classList.add('hidden');
});
document.getElementById('btn-sub-edit-submit').addEventListener('click', () => {
  const instruction = document.getElementById('sub-edit-instruction').value.trim();
  if (!instruction) { alert('修正内容を入力してください'); return; }
  document.getElementById('sub-edit-form').classList.add('hidden');
  reviewSubPhase('edit', '', instruction);
});

document.getElementById('btn-deep-dive').addEventListener('click', () => runDeepDive());

// Regular phase controls
document.getElementById('btn-run').addEventListener('click', () => runAgent(project.current_phase));
document.getElementById('btn-approve').addEventListener('click', () => submitReview('approve'));

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

// ── Start ──────────────────────────────────────────────────────────────────────
init();
