let sessionId = localStorage.getItem('firemind_session') || crypto.randomUUID();
localStorage.setItem('firemind_session', sessionId);

const missionForm = document.getElementById('missionForm');
const correctForm = document.getElementById('correctForm');
const briefBody = document.getElementById('briefBody');
const briefEngine = document.getElementById('briefEngine');
const solutionBody = document.getElementById('solutionBody');
const emptySolution = document.getElementById('emptySolution');
const statusBadge = document.getElementById('statusBadge');
const arcField = document.getElementById('arcField');
const arcHint = document.getElementById('arcHint');
const altDetails = document.getElementById('altDetails');

let lastCorrection = null;

let calcTimer = null;

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br/>');
}

function parseGameCoord(text) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const num = '[+-]?\\d+(?:[.,]\\d+)?';
  const xM = raw.match(new RegExp(`(?:^|[^a-z])x\\s*[:=]?\\s*(${num})`, 'i'));
  const yM = raw.match(new RegExp(`(?:^|[^a-z])y\\s*[:=]?\\s*(${num})`, 'i'));
  const pn = (v) => Number(String(v).replace(',', '.'));
  if (xM && yM) {
    const x = pn(xM[1]);
    const y = pn(yM[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }
  const nums = raw.match(new RegExp(num, 'g'));
  if (nums && nums.length >= 2) {
    const x = pn(nums[0]);
    const y = pn(nums[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }
  return null;
}

function applyPaste(textareaId, xId, yId) {
  const pt = parseGameCoord(document.getElementById(textareaId).value);
  if (pt) {
    document.getElementById(xId).value = pt.x;
    document.getElementById(yId).value = pt.y;
  }
}

function getWeapon() {
  const r = document.querySelector('input[name="weapon"]:checked');
  return r ? r.value : 'mortar';
}

function syncWeaponUi() {
  const spg = getWeapon() === 'spg';
  arcField.classList.toggle('hidden', !spg);
  if (arcHint) arcHint.classList.toggle('hidden', !spg);
}

function syncArcFromSolution(sol) {
  if (!sol || sol.weapon_id !== 'spg' || !sol.effective_arc) return;
  const arcEl = document.getElementById('arc');
  if (!arcEl) return;
  // 保持「自动」时不在 UI 上改成 high/low，避免下次请求误传 preferred_arc=low
  if (arcEl.value !== 'auto' && arcEl.value !== sol.effective_arc) {
    arcEl.value = sol.effective_arc;
  }
}

function formPayload() {
  applyPaste('gunPaste', 'gunX', 'gunY');
  applyPaste('targetPaste', 'targetX', 'targetY');
  const gunAlt = document.getElementById('gunAlt').value.trim();
  const targetAlt = document.getElementById('targetAlt').value.trim();
  return {
    session_id: sessionId,
    gun_x: Number(document.getElementById('gunX').value),
    gun_y: Number(document.getElementById('gunY').value),
    target_x: Number(document.getElementById('targetX').value),
    target_y: Number(document.getElementById('targetY').value),
    weapon_id: getWeapon(),
    preferred_arc: document.getElementById('arc').value,
    gun_alt: gunAlt === '' ? null : Number(gunAlt),
    target_alt: targetAlt === '' ? null : Number(targetAlt),
  };
}

function canCalculate(p) {
  return [p.gun_x, p.gun_y, p.target_x, p.target_y].every(Number.isFinite);
}

async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    const ocrBadge = document.getElementById('ocrBadge');
    const ocrPanel = document.getElementById('ocrPanel');
    if (data.ocr_enabled) {
      if (ocrBadge) {
        ocrBadge.textContent = 'OCR 已启用';
        ocrBadge.classList.add('on');
      }
    } else {
      if (ocrBadge) ocrBadge.textContent = 'OCR 未配置';
      if (ocrPanel) ocrPanel.classList.add('ocr-off');
    }
    if (data.llm_enabled) {
      statusBadge.textContent = 'LLM + 规则引擎';
      statusBadge.classList.add('llm');
    } else if (data.ocr_enabled) {
      statusBadge.textContent = 'OCR + 规则引擎';
    } else {
      statusBadge.textContent = '规则引擎（无 API Key）';
    }
  } catch (_) {}
}

let ocrImageBase64 = null;

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

function applyOcrPoint(role, pt) {
  if (!pt) return;
  if (role === 'gun') {
    document.getElementById('gunX').value = pt.x;
    document.getElementById('gunY').value = pt.y;
    document.getElementById('gunPaste').value = `x${pt.x}, y${pt.y}`;
  } else if (role === 'target') {
    document.getElementById('targetX').value = pt.x;
    document.getElementById('targetY').value = pt.y;
    document.getElementById('targetPaste').value = `x${pt.x}, y${pt.y}`;
    scheduleCalculate();
  } else if (role === 'impact') {
    const mode = document.querySelector('input[name="corrMode"]:checked')?.value || 'single';
    if (mode === 'dual' && !document.getElementById('impact1X').value) {
      document.getElementById('impact1X').value = pt.x;
      document.getElementById('impact1Y').value = pt.y;
    } else if (mode === 'dual') {
      document.getElementById('impact2X').value = pt.x;
      document.getElementById('impact2Y').value = pt.y;
    } else {
      document.getElementById('impactX').value = pt.x;
      document.getElementById('impactY').value = pt.y;
      document.getElementById('impactPaste').value = `x${pt.x}, y${pt.y}`;
    }
  }
}

function pickOcrPoint(data, role) {
  if (data.error) return { error: data.error };
  const p = data[role] || (data.points && data.points[0]);
  if (p && p.x != null && p.y != null) return { x: p.x, y: p.y };
  return { error: '未识别到十字旁 x/y，请换更清晰的地图截图' };
}

async function runOcr(role) {
  const status = document.getElementById('ocrStatus');
  if (!ocrImageBase64) {
    status.textContent = '请先选择一张地图截图';
    return;
  }
  status.textContent = '识图中…';
  try {
    const res = await fetch('/api/vision/ocr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_base64: ocrImageBase64,
        role,
        capture_mode: 'crosshair_map',
      }),
    });
    const data = await res.json();
    if (data.error && !data.points?.length) {
      status.textContent = data.error;
      return;
    }
    const pt = pickOcrPoint(data, role);
    if (pt.error) {
      status.textContent = pt.error;
      return;
    }
    applyOcrPoint(role, pt);
    status.textContent = `已填入 ${role === 'gun' ? '炮位' : role === 'target' ? '目标' : '落点'} x=${pt.x}, y=${pt.y}`;
    if (data.ocr_remaining_hour != null) {
      status.textContent += ` · 本小时 OCR 剩余约 ${data.ocr_remaining_hour} 次`;
    }
  } catch (e) {
    status.textContent = `识图失败：${e.message}`;
  }
}

function syncCorrModeUi() {
  const dual = document.querySelector('input[name="corrMode"]:checked')?.value === 'dual';
  document.getElementById('singleImpactBlock').classList.toggle('hidden', dual);
  document.getElementById('dualImpactBlock').classList.toggle('hidden', !dual);
  document.getElementById('corrHelpSingle').classList.toggle('hidden', dual);
  document.getElementById('corrHelpDual').classList.toggle('hidden', !dual);
}

async function submitDualCorrection() {
  const x1 = document.getElementById('impact1X').value.trim();
  const y1 = document.getElementById('impact1Y').value.trim();
  const x2 = document.getElementById('impact2X').value.trim();
  const y2 = document.getElementById('impact2Y').value.trim();
  if (!x1 || !y1 || !x2 || !y2) {
    briefBody.textContent = '请填写两发落点 X、Y。';
    return;
  }
  const payload = {
    session_id: sessionId,
    impact1_x: Number(x1),
    impact1_y: Number(y1),
    impact2_x: Number(x2),
    impact2_y: Number(y2),
  };
  const arcEl = document.getElementById('arc');
  if (arcEl && arcEl.value && arcEl.value !== 'auto') payload.arc = arcEl.value;
  try {
    const res = await fetch('/api/correct-dual-impact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
      briefBody.textContent = data.error;
      return;
    }
    if (data.correction) appendCorrection(data.correction);
  } catch (err) {
    briefBody.textContent += `\n两发修正失败：${err.message}`;
  }
}

function renderSolution(sol) {
  if (!sol) return;
  emptySolution.classList.add('hidden');
  solutionBody.classList.remove('hidden');

  const pairs = sol.dial_pairs || [];
  let milHtml = '';
  if (pairs.length) {
    const eff = sol.effective_arc;
    const renderPair = (p, primary) => {
      const arcLabel =
        p.arc === 'single' ? 'L81 配套读数' : p.arc === 'low' ? '低弹道配套' : '高弹道配套';
      return `
        <div class="mil-block dial-pair${primary ? ' dial-primary' : ' dial-secondary'}">
          ${primary ? '<div class="arc-badge">本次使用 — 与游戏弹道模式一致</div>' : ''}
          <div class="arc-label">${arcLabel}</div>
          <div class="dial-row">
            <div><span class="dial-k">左 RNG</span><span class="dial-v">${p.sight_rng_m} m</span></div>
            <div><span class="dial-k">右 MIL</span><span class="dial-v mil-val-inline">${p.mil}</span></div>
          </div>
          <div class="dial-hint">成对设置；RNG≈${sol.distance_m}m（平地）</div>
        </div>`;
    };
    const primaryPairs = eff ? pairs.filter((p) => p.arc === eff) : pairs.slice(0, 1);
    const secondaryPairs = eff ? pairs.filter((p) => p.arc !== eff) : pairs.slice(1);
    milHtml = primaryPairs.map((p) => renderPair(p, true)).join('');
    if (secondaryPairs.length && sol.weapon_id === 'spg') {
      milHtml += `<details class="alt-arc-details"><summary>另一弹道读数（勿混用）</summary>
        <p class="field-hint" style="margin:8px 0">在错误弹道模式下设表会导致落点偏差数百米。</p>
        ${secondaryPairs.map((p) => renderPair(p, false)).join('')}
      </details>`;
    } else {
      milHtml += secondaryPairs.map((p) => renderPair(p, false)).join('');
    }
    if (sol.weapon_id === 'spg' && eff === 'high' && sol.mil_dial > 600) {
      milHtml =
        `<div class="warn warn-critical">高弹道：右 MIL 必须在 610–1390；勿用 100 多。</div>` + milHtml;
    }
    if (sol.weapon_id === 'spg' && eff === 'low' && sol.mil_dial < 610) {
      milHtml =
        `<div class="warn warn-critical">低弹道：右 MIL 必须在 20–600；若游戏仍为【高弹道】，落点会极偏。</div>` +
        milHtml;
    }
  } else if (sol.elevation.single) {
    milHtml = `<div class="mil-block"><div class="arc-label">仰角</div><div class="mil-val">${sol.elevation.single}</div></div>`;
  }

  const allWarns = [...(sol.parse_warnings || []), ...(sol.warnings || [])];
  const warns = allWarns.map((w) => `<div class="warn">${escapeHtml(w)}</div>`).join('');
  const aim = sol.aiming_note
    ? `<div class="callout" style="background:rgba(90,159,212,0.08);border-color:rgba(90,159,212,0.3)"><strong>瞄准</strong><br/>${escapeHtml(sol.aiming_note)}</div>`
    : '';
  const arcCallout =
    sol.arc_mode_hint
      ? `<div class="callout" style="background:rgba(200,160,80,0.08);border-color:rgba(200,160,80,0.35)"><strong>弹道模式</strong><br/>${escapeHtml(sol.arc_mode_hint)}</div>`
      : '';
  const terrain = sol.terrain_note
    ? `<div class="warn">${escapeHtml(sol.terrain_note)}</div>`
    : '';
  const applied = document.getElementById('appliedDialBlock');
  const appliedHtml = applied ? applied.outerHTML : '';

  solutionBody.innerHTML = `
    ${appliedHtml}
    <div style="font-size:0.8rem;color:var(--muted);margin-bottom:4px">${escapeHtml(sol.weapon_name)}</div>
    <div style="font-size:0.78rem;color:var(--muted);margin-bottom:10px;font-family:var(--mono)">炮位 x${sol.origin.x} y${sol.origin.y}<br/>目标 x${sol.target.x} y${sol.target.y}</div>
    <div class="metric-grid">
      <div class="metric highlight">
        <div class="k">方位角</div>
        <div class="v">${sol.azimuth_deg}°</div>
      </div>
      <div class="metric">
        <div class="k">地图距离</div>
        <div class="v">${sol.distance_m} m</div>
        <div class="sub">= 左刻度 RNG（平地）</div>
      </div>
      <div class="metric">
        <div class="k">ΔX</div>
        <div class="v">${sol.dx_m >= 0 ? '+' : ''}${sol.dx_m} m</div>
      </div>
      <div class="metric">
        <div class="k">ΔY</div>
        <div class="v">${sol.dy_m >= 0 ? '+' : ''}${sol.dy_m} m</div>
      </div>
    </div>
    ${milHtml}
    ${arcCallout}
    ${terrain}
    ${warns}
    ${aim}
    <div class="callout"><strong>队频口令</strong><br/>${escapeHtml(sol.voice_callout)}</div>
  `;
  syncArcFromSolution(sol);
}

function renderAppliedDial(corr) {
  let block = document.getElementById('appliedDialBlock');
  if (!block) {
    block = document.createElement('div');
    block.id = 'appliedDialBlock';
  }
  const arcLabel = corr.arc === 'low' ? '低弹道' : corr.arc === 'high' ? '高弹道' : '';
  const parts = [];
  if (corr.new_azimuth_deg != null) parts.push(`方位 ${corr.new_azimuth_deg}°`);
  if (corr.new_rng_m != null) parts.push(`RNG ${corr.new_rng_m} m`);
  if (corr.new_mil != null) parts.push(`MIL ${Math.round(corr.new_mil)}`);
  block.className = 'applied-dial';
  block.innerHTML = `
    <strong>已采用试射修正诸元</strong>${arcLabel ? `（${arcLabel}）` : ''}<br/>
    <span class="dial-suggest" style="margin-top:6px;display:block">${escapeHtml(parts.join(' · '))}</span>
    <div class="field-hint" style="margin-top:6px">请在游戏中切到对应弹道模式后设表；此为修正建议，非射表重算。</div>`;
  if (!block.parentElement && solutionBody && !solutionBody.classList.contains('hidden')) {
    solutionBody.prepend(block);
  }
}

function applyAltitudeHint(ah) {
  const targetAlt = document.getElementById('targetAlt');
  targetAlt.value = ah.target_alt_hint_m;
  targetAlt.dataset.source = 'altitude-hint';
  if (altDetails) altDetails.open = true;
  calculate();
}

function applyCorrectionDial(corr) {
  lastCorrection = corr;
  if (corr.arc && getWeapon() === 'spg') {
    const arcEl = document.getElementById('arc');
    if (arcEl) arcEl.value = corr.arc;
  }
  renderAppliedDial(corr);
  if (solutionBody && !solutionBody.classList.contains('hidden')) {
    const existing = document.getElementById('appliedDialBlock');
    if (existing) solutionBody.prepend(existing);
  }
}

function renderBrief(reply, engine) {
  briefBody.innerHTML = escapeHtml(reply || '');
  briefEngine.textContent = engine === 'llm' ? 'LLM' : '规则引擎';
  briefEngine.classList.toggle('llm', engine === 'llm');
}

function appendCorrection(corr) {
  const box = document.createElement('div');
  box.className = 'correction-box';
  const ma = corr.miss_analysis;
  const mapBlock = ma
    ? `<div class="miss-map">
        <div><strong>地图方向</strong>：${escapeHtml(ma.map_summary)}</div>
        <div><strong>炮线方向</strong>：${escapeHtml(ma.line_summary)}</div>
        <div class="miss-delta">ΔX ${ma.dx_m} m · ΔY ${ma.dy_m} m（东+/西− · 北+/南−）</div>
      </div>`
    : '';
  const dial = [];
  if (corr.new_azimuth_deg != null) dial.push(`方位 ${Math.round(corr.new_azimuth_deg * 10) / 10}°`);
  if (corr.new_rng_m != null) dial.push(`RNG ${corr.new_rng_m} m`);
  if (corr.new_mil != null) dial.push(`MIL ${Math.round(corr.new_mil)}`);
  const ah = corr.altitude_hint;
  const altBlock = ah
    ? `<div class="alt-hint"><strong>目标海拔粗估</strong>（低置信度）<br/>
        炮位 ASL ${ah.gun_alt_m} m → 目标约 ${ah.target_alt_hint_m} m
        （ΔZ ${ah.delta_z_hint_m >= 0 ? '+' : ''}${ah.delta_z_hint_m} m）</div>`
    : '';
  const actions = [];
  if (ah) {
    actions.push(
      `<button type="button" class="btn-secondary btn-apply-alt">填入目标 ASL 并二次计算</button>`,
    );
  }
  if (corr.new_mil != null || corr.new_rng_m != null) {
    actions.push(
      `<button type="button" class="btn-secondary btn-apply-dial">采用修正 RNG/MIL</button>`,
    );
  }
  const actionRow = actions.length
    ? `<div class="correction-actions">${actions.join('')}</div>`
    : '';

  box.innerHTML = `
    <h3>试射修正</h3>
    ${mapBlock}
    ${altBlock}
    ${corr.steps.map((s) => `<div>• ${escapeHtml(s)}</div>`).join('')}
    ${dial.length ? `<div class="dial-suggest">${dial.join(' · ')}</div>` : ''}
    ${actionRow}`;

  const altBtn = box.querySelector('.btn-apply-alt');
  if (altBtn && ah) {
    altBtn.addEventListener('click', () => {
      applyAltitudeHint(ah);
      const note = document.createElement('div');
      note.className = 'field-hint';
      note.style.marginTop = '8px';
      note.textContent =
        `已填入目标 ASL 粗估 ${ah.target_alt_hint_m} m 并重新计算（低置信度；RNG/MIL 仍以射表/修正为准）。`;
      box.appendChild(note);
    });
  }
  const dialBtn = box.querySelector('.btn-apply-dial');
  if (dialBtn) {
    dialBtn.addEventListener('click', () => {
      applyCorrectionDial(corr);
    });
  }

  briefBody.appendChild(box);
}

async function calculate() {
  const payload = formPayload();
  if (!canCalculate(payload)) {
    briefBody.textContent = '请填写完整的炮位与目标 X、Y 坐标。';
    return;
  }

  briefBody.textContent = '计算中…';
  try {
    const res = await fetch('/api/calculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
      briefBody.textContent = data.error;
      return;
    }
    if (data.session_id) {
      sessionId = data.session_id;
      localStorage.setItem('firemind_session', sessionId);
    }
    renderSolution(data.solution);
    if (lastCorrection) renderAppliedDial(lastCorrection);
    renderBrief(data.reply, data.engine);
  } catch (e) {
    briefBody.textContent = `请求失败：${e.message}`;
  }
}

function scheduleCalculate() {
  clearTimeout(calcTimer);
  calcTimer = setTimeout(calculate, 450);
}

missionForm.addEventListener('submit', (e) => {
  e.preventDefault();
  calculate();
});

async function submitMissText() {
  const miss = document.getElementById('missInput').value.trim();
  if (!miss) return;
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: miss, session_id: sessionId }),
    });
    const data = await res.json();
    if (data.error) {
      briefBody.appendChild(document.createElement('div')).textContent = data.error;
      return;
    }
    if (data.correction) appendCorrection(data.correction);
  } catch (err) {
    briefBody.textContent += `\n修正失败：${err.message}`;
  }
}

correctForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  applyPaste('impactPaste', 'impactX', 'impactY');
  const ix = document.getElementById('impactX').value.trim();
  const iy = document.getElementById('impactY').value.trim();
  if (!ix || !iy) {
    briefBody.textContent = '请填写或粘贴落点 X、Y 坐标。';
    return;
  }
  try {
    const payload = {
      session_id: sessionId,
      impact_x: Number(ix),
      impact_y: Number(iy),
    };
    const arcEl = document.getElementById('arc');
    if (arcEl && arcEl.value && arcEl.value !== 'auto') payload.arc = arcEl.value;
    const res = await fetch('/api/correct-impact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
      briefBody.textContent = data.error;
      return;
    }
    if (data.correction) appendCorrection(data.correction);
  } catch (err) {
    briefBody.textContent += `\n修正失败：${err.message}`;
  }
});

document.getElementById('missTextBtn').addEventListener('click', submitMissText);

document.getElementById('impactPaste').addEventListener('blur', () => {
  applyPaste('impactPaste', 'impactX', 'impactY');
});

document.querySelectorAll('input[name="weapon"]').forEach((el) => {
  el.addEventListener('change', () => {
    syncWeaponUi();
    scheduleCalculate();
  });
});

['gunX', 'gunY', 'targetX', 'targetY', 'gunAlt', 'targetAlt', 'arc'].forEach((id) => {
  document.getElementById(id).addEventListener('input', scheduleCalculate);
});

['gunPaste', 'targetPaste'].forEach((id) => {
  document.getElementById(id).addEventListener('blur', () => {
    const map = { gunPaste: ['gunX', 'gunY'], targetPaste: ['targetX', 'targetY'] };
    applyPaste(id, map[id][0], map[id][1]);
    scheduleCalculate();
  });
});

document.getElementById('exampleMortar').addEventListener('click', () => {
  document.querySelector('input[value="mortar"]').checked = true;
  syncWeaponUi();
  document.getElementById('gunX').value = '98.43';
  document.getElementById('gunY').value = '110.38';
  document.getElementById('targetX').value = '94.53';
  document.getElementById('targetY').value = '109.03';
  document.getElementById('gunPaste').value = 'x98.43, y110.38';
  document.getElementById('targetPaste').value = 'x94.53, y109.03';
  calculate();
});

document.getElementById('exampleSph').addEventListener('click', () => {
  document.querySelector('input[value="spg"]').checked = true;
  syncWeaponUi();
  document.getElementById('gunX').value = '80.00';
  document.getElementById('gunY').value = '70.00';
  document.getElementById('targetX').value = '95.00';
  document.getElementById('targetY').value = '78.00';
  document.getElementById('arc').value = 'high';
  calculate();
});

document.getElementById('ocrFile').addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  const status = document.getElementById('ocrStatus');
  const wrap = document.getElementById('ocrPreviewWrap');
  const img = document.getElementById('ocrPreview');
  if (!file) {
    ocrImageBase64 = null;
    wrap.classList.add('hidden');
    status.textContent = '未选择图片';
    return;
  }
  ocrImageBase64 = await readFileAsDataUrl(file);
  img.src = ocrImageBase64;
  wrap.classList.remove('hidden');
  status.textContent = `已选：${file.name}`;
});

document.getElementById('ocrGunBtn').addEventListener('click', () => runOcr('gun'));
document.getElementById('ocrTargetBtn').addEventListener('click', () => runOcr('target'));
document.getElementById('ocrImpactBtn').addEventListener('click', () => runOcr('impact'));

document.querySelectorAll('input[name="corrMode"]').forEach((el) => {
  el.addEventListener('change', syncCorrModeUi);
});

document.getElementById('dualCorrectBtn').addEventListener('click', submitDualCorrection);

syncWeaponUi();
syncCorrModeUi();
checkHealth();
