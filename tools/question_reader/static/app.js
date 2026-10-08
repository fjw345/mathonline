const state = { page: 1, pageSize: 25, mode: 'problems', lastFocused: null };
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const formatNumber = (value) => Number(value || 0).toLocaleString('zh-CN');
const query = (url) => fetch(url).then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); });

function filterParams() {
  const params = new URLSearchParams();
  [['search','search'],['source-kind','source_kind'],['year','year'],['question-type','question_type'],['difficulty','difficulty'],['review','review']].forEach(([id, key]) => { const value = $(id).value.trim(); if (value) params.set(key, value); });
  params.set('page', state.page); params.set('page_size', state.pageSize);
  return params;
}

async function loadSummary() {
  const data = await query('/api/summary');
  $('metric-problems').textContent = formatNumber(data.problems);
  $('metric-sources').textContent = formatNumber(data.included_sources);
  $('metric-issues').textContent = formatNumber(data.issues);
  $('metric-issue-files').textContent = `${formatNumber(data.issue_files)} 个文件`;
  $('metric-aliases').textContent = formatNumber(data.aliases);
  $('show-issues').innerHTML = `<span aria-hidden="true">!</span> 查看识别告警（${formatNumber(data.issues)}）`;
}

function addOptions(id, values, labeler = (v) => v) {
  const select = $(id); const initial = select.options[0].outerHTML;
  select.innerHTML = initial + values.map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(labeler(item))}（${item.count}）</option>`).join('');
}

async function loadFilters() {
  const data = await query('/api/filters');
  addOptions('source-kind', data.source_kinds);
  addOptions('year', data.years);
  addOptions('question-type', data.question_types);
}

function renderTags(tags = []) { return tags.slice(0, 5).map((tag) => `<span class="tag">${escapeHtml(tag.name)}</span>`).join(''); }
function renderProblem(item) {
  const status = item.requires_review ? '待复核' : '已确认';
  return `<article class="problem-row" tabindex="0" data-id="${escapeHtml(item.id)}" role="button" aria-label="查看第 ${item.question_number} 题详情">
    <div class="problem-main"><div class="problem-title"><span class="number">Q${escapeHtml(item.question_number)}</span><span class="type">${escapeHtml(item.question_type || '未分类')}</span>${item.alias_count ? `<span class="type">${item.alias_count} 个别名</span>` : ''}</div><p class="problem-preview">${escapeHtml(item.preview || '（题干为空或未能识别）')}</p><div class="tag-line">${renderTags(item.tags)}</div></div>
    <div class="problem-source"><div class="source-name" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</div><div class="source-page">第 ${item.source_page_start}–${item.source_page_end} 页 · ${escapeHtml(item.extraction_method)}</div></div>
    <div><span class="status ${item.requires_review ? '' : 'confirmed'}">${status}</span></div>
  </article>`;
}

function renderPagination(data) {
  const holder = $('pagination'); if (data.pages <= 1) { holder.innerHTML = ''; return; }
  const buttons = []; const start = Math.max(1, Math.min(data.page - 2, data.pages - 4)); const end = Math.min(data.pages, start + 4);
  buttons.push(`<button class="page-button" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>上一页</button>`);
  for (let page = start; page <= end; page += 1) buttons.push(`<button class="page-button ${page === data.page ? 'active' : ''}" data-page="${page}">${page}</button>`);
  buttons.push(`<button class="page-button" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>下一页</button>`); holder.innerHTML = buttons.join('');
  holder.querySelectorAll('[data-page]').forEach((button) => button.addEventListener('click', () => { state.page = Number(button.dataset.page); loadProblems(); }));
}

async function loadProblems() {
  state.mode = 'problems'; $('show-issues').classList.remove('active');
  const data = await query(`/api/problems?${filterParams()}`); $('result-count').textContent = formatNumber(data.total); $('result-summary').textContent = data.total ? `第 ${(data.page - 1) * data.page_size + 1}–${Math.min(data.page * data.page_size, data.total)} 条，共 ${formatNumber(data.total)} 条` : '没有符合筛选条件的题目';
  $('problem-list').classList.remove('issue-list'); $('problem-list').innerHTML = data.items.map(renderProblem).join(''); $('empty-state').classList.toggle('hidden', data.items.length !== 0); renderPagination(data); bindRows();
}

function renderIssue(item) { return `<article class="problem-row"><div class="problem-main"><div class="problem-title"><span class="number">#${item.id}</span><span class="type">${escapeHtml(item.issue_type)}</span></div><p class="problem-preview issue-detail">${escapeHtml(item.details || '未提供详情')}</p></div><div class="problem-source"><div class="source-name" title="${escapeHtml(item.filename || '')}">${escapeHtml(item.filename || '来源未知')}</div><div class="source-page">第 ${item.page || '—'} 页 · ${escapeHtml(item.source_kind || '')}</div></div><div><span class="status">待处理</span></div></article>`; }
async function loadIssues() { state.mode = 'issues'; const data = await query(`/api/issues?page=${state.page}&page_size=${state.pageSize}`); $('result-count').textContent = formatNumber(data.total); $('result-summary').textContent = `识别告警 · 第 ${(data.page - 1) * data.page_size + 1}–${Math.min(data.page * data.page_size, data.total)} 条，共 ${formatNumber(data.total)} 条`; $('problem-list').classList.add('issue-list'); $('problem-list').innerHTML = data.items.map(renderIssue).join(''); $('empty-state').classList.toggle('hidden', data.items.length !== 0); renderPagination(data); }

function bindRows() { document.querySelectorAll('.problem-row[data-id]').forEach((row) => { row.addEventListener('click', () => openProblem(row.dataset.id)); row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openProblem(row.dataset.id); } }); }); }
function openProblem(id) { state.lastFocused = document.activeElement; $('modal-backdrop').classList.remove('hidden'); $('modal-content').innerHTML = '<p class="results-subtitle">正在读取题目详情…</p>'; $('modal-close').focus(); query(`/api/problems/${encodeURIComponent(id)}`).then(renderDetail).catch((error) => { $('modal-content').innerHTML = `<p class="issue-detail">读取失败：${escapeHtml(error.message)}</p>`; }); }
function renderDetail(item) { const options = (item.options || []).map((option) => `<div class="option"><b>${escapeHtml(option.id)}</b><span>${escapeHtml(option.text)}</span></div>`).join(''); const tags = (item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag.dimension)} · ${escapeHtml(tag.name)}</span>`).join(''); $('modal-content').innerHTML = `<p class="detail-kicker">${escapeHtml(item.external_id)}</p><h2 class="detail-title">第 ${item.question_number} 题 · ${escapeHtml(item.question_type || '未分类')}</h2><div class="detail-meta"><span>${item.requires_review ? '待复核' : '已确认'}</span><span>难度 ${item.difficulty}</span><span>${escapeHtml(item.extraction_method)}</span><span>原文件第 ${item.source_page_start}–${item.source_page_end} 页</span></div><div class="detail-section"><h3>题干</h3><p class="detail-text">${escapeHtml(item.content_markdown || '（题干为空或未能识别）')}</p></div>${options ? `<div class="detail-section"><h3>选项</h3><div class="options">${options}</div></div>` : ''}${item.standard_answer ? `<div class="detail-section"><h3>标准答案</h3><p class="detail-text">${escapeHtml(item.standard_answer)}</p></div>` : ''}${item.explanation_markdown ? `<div class="detail-section"><h3>解析</h3><p class="detail-text">${escapeHtml(item.explanation_markdown)}</p></div>` : ''}<div class="detail-section"><h3>标签</h3><div class="tag-line">${tags || '<span class="results-subtitle">暂无标签</span>'}</div></div><div class="detail-section"><h3>来源</h3><p class="detail-text"><a class="source-link" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.path)}</a></p></div>${item.aliases?.length ? `<div class="detail-section"><h3>重复来源 · ${item.aliases.length} 条</h3><p class="results-subtitle">该题已合并重复内容，原始别名仍保留在数据库中。</p></div>` : ''}`; }
function closeModal() { $('modal-backdrop').classList.add('hidden'); if (state.lastFocused) state.lastFocused.focus(); }
function scheduleSearch() { clearTimeout(scheduleSearch.timer); scheduleSearch.timer = setTimeout(() => { state.page = 1; loadProblems(); }, 220); }

document.addEventListener('DOMContentLoaded', async () => {
  try { await Promise.all([loadSummary(), loadFilters()]); await loadProblems(); } catch (error) { $('result-summary').textContent = `读取失败：${error.message}`; }
  ['source-kind','year','question-type','difficulty','review'].forEach((id) => $(id).addEventListener('change', () => { state.page = 1; loadProblems(); })); $('search').addEventListener('input', scheduleSearch);
  $('reset-filters').addEventListener('click', () => { ['search','source-kind','year','question-type','difficulty','review'].forEach((id) => { $(id).value = ''; }); state.page = 1; loadProblems(); }); $('show-issues').addEventListener('click', () => { state.page = 1; loadIssues(); }); $('modal-close').addEventListener('click', closeModal); $('modal-backdrop').addEventListener('click', (event) => { if (event.target === $('modal-backdrop')) closeModal(); }); document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { if (!$('modal-backdrop').classList.contains('hidden')) closeModal(); else if (document.activeElement !== $('search')) { $('search').focus(); $('search').select(); } } if (event.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'SELECT') { event.preventDefault(); $('search').focus(); } });
});
