'use strict';
const $ = id => document.getElementById(id);
const clone = value => structuredClone(value);
const uid = prefix => `${prefix}-${crypto.randomUUID()}`;
const clamp = (n, min = 0, max = 1) => Math.max(min, Math.min(max, n));
const roles = {stem: '题干', answer: '答案', explanation: '解析'};
const draftKey = `mathonline-cropper-v1:${location.origin}`;
let sample, state, pageId, questionId, regionId, mode = 'select', zoom = 1;
let history = [], future = [], serial = 0, savedSerial = 0, saveTimer, saving, gesture, backup;
let previewToken = 0, imageToken = 0, exportBusy = false;
const images = new Map();
const currentPage = () => sample.pages.find(p => p.id === pageId);
const currentQuestion = () => state.questions.find(q => q.id === questionId);
const currentRegion = () => currentQuestion()?.regions.find(r => r.id === regionId);
const currentDocument = () => sample.documents.find(d => d.id === currentPage().document_id);
const snapshot = () => clone({questions: state.questions, page_notes: state.page_notes});

function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
function textNode(tag, text, className = '') { const e = document.createElement(tag); e.textContent = text; e.className = className; return e; }
function button(text, action, className = '') { const e = textNode('button', text, className); e.type = 'button'; e.onclick = action; return e; }
function persistDraft() {
  try { localStorage.setItem(draftKey, JSON.stringify(state)); }
  catch (_) { notice('浏览器草稿备份不可用，请保持页面打开并使用“立即保存”。'); }
}
function dirty() {
  serial++; persistDraft(); $('save-status').textContent = '有修改，等待保存…';
  $('save-status').classList.remove('error'); clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveNow().catch(() => {}), 650);
}
function commit(before) {
  if (JSON.stringify(before) === JSON.stringify(snapshot())) return;
  history.push(before); if (history.length > 70) history.shift(); future = [];
  dirty(); render();
}
function mutate(fn, invalidate = true) {
  const before = snapshot(); fn();
  if (invalidate && currentQuestion()) currentQuestion().reviewed = false;
  commit(before);
}
async function saveNow() {
  clearTimeout(saveTimer);
  if (saving) return saving;
  saving = (async () => {
    while (savedSerial < serial) {
      const target = serial;
      $('save-status').textContent = '正在保存…';
      try {
        const res = await fetch('/api/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state)});
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || '保存失败');
        state.revision = result.revision; state.updated_at = result.updated_at; savedSerial = target;
        if (serial === target) { try { localStorage.removeItem(draftKey); } catch (_) {} }
        else persistDraft();
        $('save-status').textContent = `已保存 · ${result.updated_at.slice(-8)}`;
        $('save-status').classList.remove('error');
      } catch (error) {
        $('save-status').textContent = '保存失败，请重试'; $('save-status').classList.add('error');
        notice(`${error.message} 当前修改仍保留在页面中。`); persistDraft(); throw error;
      }
    }
  })();
  try { await saving; } finally { saving = null; }
}

function loadImage(id) {
  if (!images.has(id)) {
    const promise = new Promise((resolve, reject) => {
      const img = new Image(); img.onload = () => resolve(img);
      img.onerror = () => { images.delete(id); reject(new Error('原页图片加载失败，请刷新重试。')); };
      img.src = `/api/pages/${id}.png`;
    });
    images.set(id, promise);
  }
  return images.get(id);
}
async function showPage(id, preserveSelection = false) {
  pageId = id; zoom = 1; setZoom();
  if (!preserveSelection) {
    const q = state.questions.find(q => q.regions.some(r => r.page_id === id));
    questionId = q?.id; regionId = q?.regions.find(r => r.page_id === id)?.id;
  } else if (currentQuestion()) {
    regionId = currentQuestion().regions.find(r => r.page_id === id)?.id;
  }
  const token = ++imageToken;
  $('page-stage').style.aspectRatio = `${currentPage().width} / ${currentPage().height}`;
  $('page-image').style.visibility = 'hidden'; $('overlay').style.visibility = 'hidden';
  $('page-scroll').scrollTo(0, 0); render();
  try {
    const img = await loadImage(id); if (token !== imageToken) return;
    $('page-image').src = img.src;
    $('page-image').alt = `${currentDocument().name}，原卷第 ${currentPage().number} 页`;
    $('page-image').style.visibility = 'visible'; $('overlay').style.visibility = 'visible';
  } catch (error) { notice(error.message); }
}
function selectQuestion(id, rid, navigate = true) {
  questionId = id; const q = currentQuestion(); if (!q) { render(); return; }
  const r = q.regions.find(r => r.id === rid) || q.regions.find(r => r.page_id === pageId) || q.regions[0];
  regionId = r.id;
  if (navigate && r.page_id !== pageId) { showPage(r.page_id, true).then(() => { regionId = r.id; render(); }); }
  else render();
}
function setMode(next) {
  if (['append', 'split'].includes(next) && !currentQuestion()) { notice('先选择一道题。'); return; }
  mode = next;
  document.querySelectorAll('[data-mode]').forEach(b => { b.classList.toggle('active', b.dataset.mode === mode); b.setAttribute('aria-pressed', b.dataset.mode === mode); });
  $('overlay').classList.toggle('drawing', mode !== 'select');
  const q = currentQuestion();
  $('mode-hint').textContent = ({select: '拖动框的边缘或角点调整范围，拖动框内移动。', new: '在原页上拖出一个矩形，创建新题。', append: `为第 ${q?.number || ''} 题补画片段。可先切换同一试卷的页面，再画框。`, split: '在所选单片段题目的两题之间点击，生成上下两道题。拆分后仍可拖动边缘。'})[mode];
}
function setZoom() { $('page-stage').style.width = `${zoom * 100}%`; $('zoom-label').textContent = `${Math.round(zoom * 100)}%`; }
function render() {
  if (!state) return;
  const page = currentPage(), doc = currentDocument(), q = currentQuestion();
  $('document').value = doc.id; $('document-hint').textContent = doc.hint;
  $('progress').textContent = `${state.questions.filter(q => q.reviewed).length} / ${state.questions.length} 已确认`;
  $('page-kicker').textContent = doc.title; $('page-title').textContent = `${doc.name} · 第 ${page.number} 页`;
  $('page-position').textContent = `样本 ${sample.pages.findIndex(p => p.id === pageId) + 1} / ${sample.pages.length} 页`;
  $('page-note').value = state.page_notes[pageId] || '';
  $('page-list').replaceChildren(...sample.pages.filter(p => p.document_id === doc.id).map(p => {
    const e = button('', () => showPage(p.id, mode === 'append'), `page-tile ${p.id === pageId ? 'active' : ''}`);
    e.setAttribute('aria-label', `原卷第 ${p.number} 页`); e.setAttribute('aria-pressed', p.id === pageId);
    const img = new Image(); img.src = `/api/pages/${p.id}.png`; img.alt = ''; img.loading = 'lazy';
    e.append(img, textNode('span', `第 ${p.number} 页`)); return e;
  }));
  const onPage = state.questions.filter(q => q.regions.some(r => r.page_id === pageId));
  $('page-count').textContent = `${onPage.length} 题`;
  $('question-list').replaceChildren(...onPage.map(item => {
    const e = button('', () => { setMode('select'); selectQuestion(item.id); }, `question-row ${item.id === questionId ? 'active' : ''}`);
    e.setAttribute('aria-pressed', item.id === questionId);
    e.append(textNode('span', `第 ${item.number} 题`), textNode('small', item.reviewed ? '已确认' : item.regions.length > 1 ? `${item.regions.length} 片段` : '待检查', item.reviewed ? 'done-text' : ''));
    return e;
  }));
  if (!onPage.length) $('question-list').append(textNode('p', '本页还没有题框，可使用“框选新题”。', 'muted'));
  $('undo').disabled = !history.length; $('redo').disabled = !future.length;
  $('no-selection').hidden = !!q; $('selection-panel').hidden = !q;
  renderOverlay(); renderInspector(); setMode(mode);
}
function positionBox(el, box) { el.style.left = `${box[0]*100}%`; el.style.top = `${box[1]*100}%`; el.style.width = `${(box[2]-box[0])*100}%`; el.style.height = `${(box[3]-box[1])*100}%`; }
function renderOverlay() {
  $('overlay').replaceChildren();
  for (const q of state.questions) for (const r of q.regions) {
    if (r.page_id !== pageId) continue;
    const selected = r.id === regionId;
    const el = document.createElement('div'); el.className = `region ${selected ? 'selected' : ''} ${q.reviewed ? 'reviewed' : ''}`;
    el.dataset.region = r.id; el.dataset.question = q.id; el.tabIndex = 0; el.setAttribute('role', 'button');
    el.setAttribute('aria-label', `第 ${q.number} 题 ${roles[r.role]}区域，回车选择，方向键移动`);
    positionBox(el, r.bbox); el.append(textNode('span', `${q.number} · ${roles[r.role]}${q.reviewed ? ' ✓' : ''}`, 'region-label'));
    el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectQuestion(q.id, r.id); $('page-scroll').focus({preventScroll:true}); } };
    if (selected) for (const handle of ['nw','n','ne','e','se','s','sw','w']) {
      const dot = document.createElement('span'); dot.className = 'handle'; dot.dataset.handle = handle; dot.setAttribute('aria-hidden', 'true'); el.append(dot);
    }
    $('overlay').append(el);
  }
}
function renderInspector() {
  const q = currentQuestion(); if (!q) { previewToken++; $('preview').replaceChildren(); return; }
  const r = currentRegion();
  $('review-badge').textContent = q.reviewed ? '边界已确认' : '待检查'; $('review-badge').classList.toggle('done', q.reviewed);
  $('preview-title').textContent = `第 ${q.number} 题`; $('part-count').textContent = `${q.regions.length} 个片段`;
  $('number').value = q.number; $('kind').value = q.kind; $('answer').value = q.answer;
  $('tags').value = q.tags.join('，'); $('question-note').value = q.note;
  $('unconfirm').hidden = !q.reviewed;
  $('region-list').replaceChildren(...q.regions.map((part, i) => {
    const row = document.createElement('div'); row.className = 'part-row';
    const p = sample.pages.find(p => p.id === part.page_id);
    row.append(button(`${i+1}. 第 ${p.number} 页 · ${roles[part.role]}`, () => { setMode('select'); selectQuestion(q.id, part.id); }, `part-select ${part.id === regionId ? 'active' : ''}`));
    for (const [delta, symbol, label] of [[-1,'↑','上移'],[1,'↓','下移']]) {
      const move = button(symbol, () => mutate(() => { [q.regions[i], q.regions[i+delta]] = [q.regions[i+delta], q.regions[i]]; }), 'reorder');
      move.disabled = i+delta < 0 || i+delta >= q.regions.length; move.setAttribute('aria-label', `${label}第 ${i+1} 个片段`); row.append(move);
    }
    return row;
  }));
  $('role').disabled = !r; $('role').value = r?.role || 'stem'; $('remove-region').disabled = !r;
  document.querySelectorAll('[data-coordinate]').forEach(el => { el.disabled = !r; el.value = r ? +(r.bbox[+el.dataset.coordinate]*100).toFixed(2) : ''; });
  const targets = state.questions.filter(other => other.document_id === q.document_id && other.id !== q.id);
  $('merge-target').replaceChildren(...targets.map(other => { const opt = textNode('option', `第 ${other.number} 题 · ${other.regions.length} 片段`); opt.value = other.id; return opt; }));
  $('merge').disabled = !targets.length;
  renderPreview();
}
async function renderPreview() {
  const token = ++previewToken, q = currentQuestion(); if (!q) return;
  const regions = clone(q.regions);
  try {
    const sourceImages = await Promise.all(regions.map(r => loadImage(r.page_id)));
    if (token !== previewToken) return;
    const pieces = regions.map((r, i) => {
      const source = sourceImages[i], b = r.bbox;
      const x = Math.floor(b[0]*source.width), y = Math.floor(b[1]*source.height);
      const w = Math.ceil(b[2]*source.width)-x, h = Math.ceil(b[3]*source.height)-y;
      const wrapper = document.createElement('div'); wrapper.className = 'preview-piece';
      const p = sample.pages.find(p => p.id === r.page_id);
      wrapper.append(textNode('div', `${i+1} · 原卷第 ${p.number} 页 · ${roles[r.role]}`, 'preview-caption'));
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1,w); canvas.height = Math.max(1,h);
      canvas.setAttribute('role','img'); canvas.setAttribute('aria-label', `第 ${q.number} 题第 ${i+1} 个裁剪片段`);
      canvas.getContext('2d').drawImage(source, x, y, w, h, 0, 0, w, h); wrapper.append(canvas); return wrapper;
    });
    $('preview').replaceChildren(...pieces);
  } catch (error) { notice(error.message); }
}

function point(e) { const rect = $('overlay').getBoundingClientRect(); return [clamp((e.clientX-rect.left)/rect.width), clamp((e.clientY-rect.top)/rect.height)]; }
function nextNumber(docId) { return String(Math.max(0, ...state.questions.filter(q => q.document_id === docId).map(q => parseInt(q.number, 10) || 0)) + 1); }
function newQuestion(region, number) {
  const doc = currentDocument();
  return {id:uid('q'), document_id:doc.id, number:number || nextNumber(doc.id), kind:'unknown', answer:'', tags:[...doc.tags], reviewed:false, note:'', regions:[region]};
}
$('overlay').addEventListener('pointerdown', e => {
  if (e.button !== 0 || gesture) return;
  const start = point(e), target = e.target.closest('[data-region]');
  if (mode === 'split') {
    const q = currentQuestion(), r = currentRegion();
    if (!r || r.page_id !== pageId || q.regions.length !== 1 || r.role !== 'stem') { notice('点线拆题用于单个题干框。跨页题请先选中其他单片段题目，或使用框选新题。'); return; }
    const [x0,y0,x1,y1] = r.bbox, [x,y] = start;
    if (x < x0 || x > x1 || y < y0+.005 || y > y1-.005) { notice('请在所选框内、两道题的空白间隔处点击。'); return; }
    mutate(() => {
      const second = newQuestion({id:uid('r'),page_id:pageId,bbox:[x0,y,x1,y1],role:'stem'}, `${q.number}-2`);
      r.bbox[3] = y; const index = state.questions.indexOf(q); state.questions.splice(index+1,0,second);
    });
    setMode('select'); notice('已拆为两题。请调整题号并核对分割处；可撤销。'); return;
  }
  if (mode === 'select') {
    if (!target) return;
    const handle = e.target.dataset.handle;
    selectQuestion(target.dataset.question,target.dataset.region,false);
    gesture = {type:'edit',start,box:[...currentRegion().bbox],handle,before:snapshot(),pointer:e.pointerId};
  } else {
    if (mode === 'append' && currentQuestion()?.document_id !== currentPage().document_id) { notice('请在同一份试卷中补充区域。'); return; }
    gesture = {type:mode,start,before:snapshot(),pointer:e.pointerId,box:[...start,...start]};
    const temp = document.createElement('div'); temp.id = 'draft-region'; temp.className = 'region draft-region'; $('overlay').append(temp);
  }
  e.preventDefault(); $('overlay').setPointerCapture(e.pointerId); $('page-scroll').focus({preventScroll:true});
});
$('overlay').addEventListener('pointermove', e => {
  if (!gesture || e.pointerId !== gesture.pointer) return;
  const p = point(e), g = gesture;
  if (g.type === 'edit') {
    const b = [...g.box], dx = p[0]-g.start[0], dy = p[1]-g.start[1], h = g.handle;
    if (!h) { const sx = clamp(dx,-b[0],1-b[2]), sy = clamp(dy,-b[1],1-b[3]); b[0]+=sx; b[2]+=sx; b[1]+=sy; b[3]+=sy; }
    else { if(h.includes('w'))b[0]=clamp(b[0]+dx,0,b[2]-.003); if(h.includes('e'))b[2]=clamp(b[2]+dx,b[0]+.003,1); if(h.includes('n'))b[1]=clamp(b[1]+dy,0,b[3]-.003); if(h.includes('s'))b[3]=clamp(b[3]+dy,b[1]+.003,1); }
    currentRegion().bbox = b;
    const el = $('overlay').querySelector('.selected'); if (el) positionBox(el,b);
    renderPreview();
  } else {
    g.box = [Math.min(g.start[0],p[0]),Math.min(g.start[1],p[1]),Math.max(g.start[0],p[0]),Math.max(g.start[1],p[1])];
    positionBox($('draft-region'),g.box);
  }
});
function endGesture(e, cancelled = false) {
  if (!gesture || e.pointerId !== gesture.pointer) return;
  const g = gesture; gesture = null;
  if ($('overlay').hasPointerCapture(e.pointerId)) $('overlay').releasePointerCapture(e.pointerId);
  if (cancelled) { Object.assign(state,g.before); render(); return; }
  if (g.type === 'edit') {
    if (JSON.stringify(g.before) !== JSON.stringify(snapshot())) currentQuestion().reviewed = false;
    commit(g.before);
  } else if (g.box[2]-g.box[0] >= .005 && g.box[3]-g.box[1] >= .005) {
    const r = {id:uid('r'),page_id:pageId,bbox:g.box,role:'stem'};
    if (g.type === 'new') { const q = newQuestion(r); state.questions.push(q); questionId=q.id; }
    else { currentQuestion().regions.push(r); currentQuestion().reviewed=false; }
    regionId = r.id; commit(g.before); setMode('select');
  } else render();
}
$('overlay').addEventListener('pointerup', e => endGesture(e));
$('overlay').addEventListener('pointercancel', e => endGesture(e,true));

for (const id of ['number','kind','answer','question-note','tags']) $(id).addEventListener('change', () => {
  const q = currentQuestion(); if (!q) return;
  const key = {'question-note':'note'}[id] || id;
  const value = id === 'tags' ? $(id).value.split(/[,，]/).map(v => v.trim()).filter(Boolean) : $(id).value.trim();
  if (id === 'number' && !value) { $(id).value=q.number; notice('题号不能为空。'); return; }
  if (id === 'tags' && (value.length > 30 || value.some(t => t.length > 100))) { notice('请控制在 30 个标签以内，每个不超过 100 字。'); renderInspector(); return; }
  mutate(() => { q[key]=value; });
});
$('page-note').addEventListener('change', () => mutate(() => { state.page_notes[pageId]=$('page-note').value.slice(0,4000); },false));
$('role').addEventListener('change', () => {
  const q=currentQuestion(), r=currentRegion(), role=$('role').value;
  if (r.role==='stem' && role!=='stem' && q.regions.filter(z=>z.role==='stem').length===1) { notice('每道题至少保留一个题干区域。先补画题干，再更改此片段用途。'); $('role').value=r.role; return; }
  mutate(()=>{r.role=role;});
});
document.querySelectorAll('[data-coordinate]').forEach(el => el.addEventListener('change', () => {
  const r=currentRegion(); if(!r)return;
  const b=[...r.bbox]; b[+el.dataset.coordinate]=Number(el.value)/100;
  if(el.value==='' || b.some(x=>!Number.isFinite(x)||x<0||x>1) || b[2]-b[0]<.002 || b[3]-b[1]<.002) {notice('请输入 0–100 之间的边界，右边应大于左边，下边应大于上边。');renderInspector();return;}
  mutate(()=>{r.bbox=b;});
}));
$('remove-region').onclick=()=>{
  const q=currentQuestion(),r=currentRegion();if(!r)return;
  if(q.regions.length>1 && r.role==='stem' && q.regions.filter(z=>z.role==='stem').length===1){notice('先将另一个片段设为题干，再删除此片段。');return;}
  const before=snapshot();q.regions=q.regions.filter(z=>z.id!==r.id);q.reviewed=false;
  if(!q.regions.length){state.questions=state.questions.filter(z=>z.id!==q.id);questionId=state.questions.find(z=>z.regions.some(p=>p.page_id===pageId))?.id;}
  regionId=currentQuestion()?.regions[0]?.id;commit(before);notice('已删除片段，可点击“撤销”恢复。');
};
$('merge').onclick=()=>{
  const q=currentQuestion(),other=state.questions.find(q=>q.id===$('merge-target').value);if(!other)return;
  if(other.answer || other.note || other.regions.some(r=>r.role!=='stem')) {notice('另一题含有答案、解析或备注，请先处理这些内容再合并，避免丢失。');return;}
  mutate(()=>{q.regions.push(...other.regions);q.tags=[...new Set([...q.tags,...other.tags])];state.questions=state.questions.filter(z=>z.id!==other.id);});
  notice('已并入当前题。请用片段旁的上下按钮核对顺序，并调整题号。');
};
function undo(redo=false){
  if(gesture)return;const from=redo?future:history,to=redo?history:future;if(!from.length)return;
  to.push(snapshot());Object.assign(state,from.pop());
  if(!currentQuestion())questionId=state.questions.find(q=>q.regions.some(r=>r.page_id===pageId))?.id;
  if(!currentRegion())regionId=currentQuestion()?.regions.find(r=>r.page_id===pageId)?.id||currentQuestion()?.regions[0]?.id;
  dirty();render();notice('');
}
$('undo').onclick=()=>undo();$('redo').onclick=()=>undo(true);
$('confirm').onclick=()=>{
  const q=currentQuestion();if(!q)return;
  mutate(()=>{q.reviewed=true;},false);
  const group=state.questions.filter(z=>z.document_id===q.document_id),index=group.indexOf(q);
  const next=[...group.slice(index+1),...group.slice(0,index)].find(z=>!z.reviewed);
  if(next){setMode('select');selectQuestion(next.id,next.regions[0].id);}
  else notice('本组题目已全部确认，可以导出或切换下一份试卷。');
};
$('unconfirm').onclick=()=>mutate(()=>{currentQuestion().reviewed=false;},false);
$('save').onclick=()=>saveNow().then(()=>notice('')).catch(()=>{});
$('export').onclick=async()=>{
  if(exportBusy)return;
  if(!state.questions.some(q=>q.reviewed)){notice('请先确认至少一道题，再导出。');return;}
  exportBusy=true;$('export').disabled=true;$('export').textContent='正在生成题图…';
  try{
    await saveNow();
    const response=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:state.revision})});
    if(!response.ok)throw new Error((await response.json()).error);
    download(await response.blob(),'question-crops.zip');notice('导出完成。ZIP 内含已确认题图、坐标和测试记录；本机 exports 文件夹也保留了一份。');
  }catch(error){notice(`导出失败：${error.message}`);}finally{exportBusy=false;$('export').disabled=false;$('export').textContent='导出已确认题目';}
};
function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{notice('');setMode(b.dataset.mode);});
$('document').onchange=()=>{setMode('select');showPage(sample.pages.find(p=>p.document_id===$('document').value).id);};
$('zoom-in').onclick=()=>{zoom=clamp(zoom+.25,.5,2.5);setZoom();};
$('zoom-out').onclick=()=>{zoom=clamp(zoom-.25,.5,2.5);setZoom();};
$('zoom-fit').onclick=()=>{zoom=1;setZoom();};
document.addEventListener('keydown',e=>{
  if(!state || e.target.closest('input,select,textarea'))return;
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();undo(e.shiftKey);return;}
  if(e.key==='Escape'){if(gesture)endGesture({pointerId:gesture.pointer},true);setMode('select');return;}
  if(!e.target.closest('#page-scroll')||!currentRegion()||currentRegion().page_id!==pageId||gesture)return;
  if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
  e.preventDefault();const dx=({'ArrowLeft':-.001,'ArrowRight':.001}[e.key]||0),dy=({'ArrowUp':-.001,'ArrowDown':.001}[e.key]||0);
  mutate(()=>{const b=currentRegion().bbox;if(e.shiftKey){b[2]=clamp(b[2]+dx,b[0]+.003,1);b[3]=clamp(b[3]+dy,b[1]+.003,1);}else{const x=clamp(dx,-b[0],1-b[2]),y=clamp(dy,-b[1],1-b[3]);b[0]+=x;b[2]+=x;b[1]+=y;b[3]+=y;}});
});
window.addEventListener('beforeunload',e=>{if(state&&serial>savedSerial){persistDraft();e.preventDefault();e.returnValue='';}});
$('restore').onclick=()=>{
  if(!backup)return;
  const before=snapshot();state.questions=backup.questions;state.page_notes=backup.page_notes;
  questionId=state.questions.find(q=>q.regions.some(r=>r.page_id===pageId))?.id;regionId=currentQuestion()?.regions[0]?.id;
  commit(before);$('recovery').hidden=true;notice('已恢复本地草稿，将保存到当前版本。请核对内容。');
};
$('download-draft').onclick=()=>download(new Blob([JSON.stringify(backup||state,null,2)],{type:'application/json'}),'cropper-local-draft.json');
$('ignore-draft').onclick=()=>{localStorage.removeItem(draftKey);$('recovery').hidden=true;backup=null;};
async function init(){
  try{
    const response=await fetch('/api/state');const data=await response.json();if(!response.ok)throw new Error(data.error);
    sample=data.sample;state=data.state;
    $('document').replaceChildren(...sample.documents.map(d=>{const opt=textNode('option',d.title);opt.value=d.id;return opt;}));
    try{backup=JSON.parse(localStorage.getItem(draftKey));}catch(_){backup=null;}
    if(backup&&Array.isArray(backup.questions)&&backup.page_notes&&JSON.stringify({questions:backup.questions,page_notes:backup.page_notes})!==JSON.stringify(snapshot())){
      $('recovery').hidden=false;
      if(backup.revision!==state.revision){$('restore').disabled=true;$('recovery').querySelector('span').textContent='发现其他版本的本地草稿。可先下载备份，避免覆盖磁盘上的新修改。';}
    }
    $('save-status').textContent=state.updated_at?`已保存 · ${state.updated_at.slice(-8)}`:'初框已准备，等待检查';
    await showPage(sample.pages[0].id);$('workspace').setAttribute('aria-busy','false');
  }catch(error){notice(`加载失败：${error.message}`);$('save-status').textContent='加载失败';}
}
init();
