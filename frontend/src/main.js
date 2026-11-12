import './style.css';
import { initSetup } from './setup.js';

const $ = (selector) => document.querySelector(selector);
const state = { project: null, version: null, checks: null, selectedScene: null, busy: false, history: [] };
const escape = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = async (method, path, body) => {
  const response = await fetch(path, {method, headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
  let data;
  try { data = await response.json(); } catch { throw new Error(`HTTP ${response.status}: JSON 응답이 아닙니다.`); }
  if (!response.ok) throw new Error(data.error_message || data.detail?.message || (typeof data.detail === 'string' ? data.detail : JSON.stringify(data)) || `HTTP ${response.status}`);
  return data;
};
const statusName = (s) => ({STRUCTURING:'요구사항 분석 중',DRAFT:'구조화 완료',QUEUED:'생성 대기',PROCESSING:'생성 중',COMPLETED:'생성 완료',FAILED:'실패'})[s] || s || '미생성';
const notify = (message, type='info') => { const el=$('#notice'); el.textContent=message; el.className='notice '+type; el.hidden=false; };
const setBusy = (v) => {state.busy=v; document.querySelectorAll('button.action').forEach(b=>b.disabled=v)};
const fmtMs = n => `${((n || 0)/1000).toFixed(1)}초`;

$('#app').innerHTML = `
<header class="topbar"><div class="logo">F<span>R</span>AME<span class="dot">●</span></div><div class="top-right"><span class="pill">Re:Frame · Graduation Project</span><span id="connection" class="connection">서버 확인 중</span></div></header>
<main class="layout">
  <aside class="sidebar"><div class="side-title">WORKSPACE</div><nav><a class="active" href="#workspace">▦ &nbsp; 프로젝트 관리</a><a href="#scenes">▣ &nbsp; 스토리보드</a><a href="#checks">✓ &nbsp; 품질 검수</a><a href="#revisions">↺ &nbsp; 선택적 수정</a></nav><div class="side-bottom"><strong>FRAME Studio</strong><p>요구사항 구조화 → 생성 → 검수 → 수정 버전 관리</p><small>백엔드: project_videoManager / dev</small></div></aside>
  <div class="content"><div class="heading"><div><div class="eyebrow">AI VIDEO WORKFLOW</div><h1>영상 프로젝트 워크스페이스</h1><p>요구사항부터 생성 결과까지 하나의 흐름으로 관리합니다.</p></div><span class="preview-tag">API CONNECTED MVP</span></div>
    <div class="notice" id="notice" hidden></div>
    <section class="main-grid" id="workspace">
      <article class="panel"><div class="panel-title"><h2>01 · 새 프로젝트 생성</h2><span id="input-mode">자연어 요구사항</span></div><div id="llm-key-panel" class="key-panel" hidden></div><form id="create-form"><label>시연 프리셋<select id="preset-select"><option value="">직접 입력</option></select></label><label>프로젝트 제목<input name="title" placeholder="예: 카페 신제품 15초 숏폼" required value="카페 신제품 숏폼"/></label><label>영상 기획안<textarea name="prompt" rows="8" required placeholder="장면별 내용, 등장 객체와 속성을 작성하세요.">카페 신제품 숏폼. 장면 1 (4초): 검은 머리의 바리스타가 빨간 머그컵을 들어 보인다. 장면 2 (4초): 빨간 머그컵을 클로즈업한다. 장면 3 (4초): 바리스타가 손을 흔든다.</textarea></label><label>목표 길이 (초)<input type="number" name="duration" min="1" max="300" value="12"/></label><button class="primary action" type="submit">요구사항 분석 시작 →</button></form></article>
      <article class="panel"><div class="panel-title"><h2>02 · 현재 버전</h2><span id="state-badge" class="state">프로젝트 없음</span></div><div class="metadata"><div><small>PROJECT ID</small><strong id="project-id">—</strong></div><div><small>VERSION ID</small><strong id="version-id">—</strong></div></div><div class="inline-controls"><input id="open-id" placeholder="기존 Version ID 입력"/><button class="secondary action" id="open-button">불러오기</button></div><div class="summary"><div><small>장면</small><strong id="scene-count">0</strong></div><div><small>엔티티</small><strong id="entity-count">0</strong></div><div><small>요구조건</small><strong id="req-count">0</strong></div></div><div class="inline-controls"><button class="primary action" id="generate-button" disabled>영상 생성 요청</button><button class="secondary action" id="refresh-button" disabled>상태 새로고침</button></div><div id="version-error" class="error"></div></article>
    </section>
    <section class="panel wide" id="scenes"><div class="panel-title"><h2>03 · 스토리보드 & 구조화 결과</h2><span>IR · Scene / Entity / Requirement</span></div><div id="scene-list" class="scenes empty">프로젝트를 생성하면 장면과 요구조건이 이곳에 표시됩니다.</div></section>
    <section class="bottom-grid"><article class="panel" id="revisions"><div class="panel-title"><h2>04 · 선택적 수정</h2><span>새 버전 생성</span></div><p class="hint">원본 버전은 보존하고 변경된 요구조건을 가진 새 DRAFT 버전을 만듭니다.</p><form id="revision-form"><label>수정할 객체<select name="entity_key" id="entity-select" required><option value="">객체를 먼저 선택하세요</option></select></label><label>속성 또는 관계<select name="predicate" id="predicate-select" required><option value="">속성을 선택하세요</option></select></label><label>변경 값<input name="new_value" required placeholder="예: blue"/></label><label>적용 범위<select name="scope" id="scope"><option value="ALL_APPEARANCES">모든 등장 장면</option><option value="SCENES">선택한 장면</option></select></label><label>선택한 장면 (SCENES일 때)<select id="scene-select"><option value="">장면 선택</option></select></label><button type="submit" class="primary action">수정 버전 만들기</button></form><div id="revision-result"></div></article>
    <article class="panel" id="checks"><div class="panel-title"><h2>05 · 품질 검수</h2><span>Requirements / Consistency</span></div><div class="stats"><div><small>요구사항 반영률</small><strong id="metric-reflection">—</strong></div><div><small>일관성</small><strong id="metric-consistency">—</strong></div><div><small>비대상 보존율</small><strong id="metric-preservation">—</strong></div></div><div class="inline-controls"><button class="secondary action" id="checks-button" disabled>검수 결과 불러오기</button></div><div id="checks-list" class="checks-empty">검수 결과가 없습니다.</div><p class="hint">현재 서버의 실제 검수 결과만 표시합니다. Fake 모드에서는 실영상 성능을 의미하지 않습니다.</p></article></section>
    <footer>FRAME Studio · 기반 API: sw1029/project_videoManager dev · OpenAI API 키는 브라우저에 저장하지 않고 이 기기의 서버 메모리에만 둡니다.</footer>
  </div>
</main>`;

const pct=n=>Number.isFinite(n)?`${Math.round(n*100)}%`:'—';
function render() {
  const v=state.version;
  $('#project-id').textContent = v?.project_id || state.project?.project_id || '—';
  $('#version-id').textContent = v?.version_id || state.project?.version_id || '—';
  $('#state-badge').textContent = statusName(v?.state || state.project?.state);
  $('#state-badge').dataset.state=v?.state || '';
  const scenes=v?.ir?.scenes||[], entities=v?.ir?.entities||[];
  $('#scene-count').textContent=scenes.length;
  $('#entity-count').textContent=entities.length;
  $('#req-count').textContent=scenes.reduce((sum,s)=>sum+(s.requirements?.length||0),0);
  $('#generate-button').disabled=state.busy || v?.state!=='DRAFT';
  $('#refresh-button').disabled=state.busy || !v;
  $('#checks-button').disabled=state.busy || !v;
  $('#version-error').textContent=v?.state==='FAILED' ? (v.error_message||v.error_code||'실패') : '';
  $('#scene-list').className=scenes.length?'scenes':'scenes empty';
  $('#scene-list').innerHTML = scenes.length?scenes.map((s,i)=>`<div class="scene-card"><div class="scene-meta"><span class="num">${String(i+1).padStart(2,'0')}</span><div><strong>${escape(s.scene_key)}</strong><small>${fmtMs(s.planned_start_ms)} ~ ${fmtMs(s.planned_end_ms)}</small></div><span class="count">요구조건 ${s.requirements?.length||0}개</span></div><p>${escape(s.description)}</p><div class="reqs">${(s.requirements||[]).map(r=>`<span class="req">${escape(r.entity_key || '장면')} · ${escape(r.predicate)} : ${escape(r.expected_value)}</span>`).join('') || '<span class="muted">장면 요구조건 없음</span>'}</div>${s.clip_uri?`<div class="clip">클립 URI: ${escape(s.clip_uri)}</div>`:''}</div>`).join(''):'프로젝트를 생성하면 장면과 요구조건이 이곳에 표시됩니다.';
  const curEnt=$('#entity-select').value, curScene=$('#scene-select').value, curPredicate=$('#predicate-select').value;
  $('#entity-select').innerHTML='<option value="">객체를 선택하세요</option>'+entities.map(e=>`<option value="${escape(e.entity_key)}">${escape(e.entity_key)} (${escape(e.kind)})</option>`).join('');
  if(entities.some(e=>e.entity_key===curEnt)) $('#entity-select').value=curEnt;
  $('#scene-select').innerHTML='<option value="">장면 선택</option>'+scenes.map(s=>`<option value="${escape(s.scene_key)}">${escape(s.scene_key)}</option>`).join('');
  if(scenes.some(s=>s.scene_key===curScene)) $('#scene-select').value=curScene;
  updatePredicates(curPredicate);
  const sm=state.checks?.summary;
  $('#metric-reflection').textContent=pct(sm?.requirement_reflection_rate);
  $('#metric-consistency').textContent=pct(sm?.consistency_rate);
  $('#metric-preservation').textContent=pct(sm?.preservation_rate);
  $('#checks-list').innerHTML=state.checks?.results?.length?state.checks.results.map(r=>`<div class="check-row"><span class="verdict ${escape(r.verdict)}">${escape(r.verdict)}</span><div><strong>${escape(r.check_type)}</strong><small>${escape(r.scene_key||r.entity_key||r.requirement_key||'전체')} · 점수 ${r.score==null?'—':escape(r.score)}</small>${(r.warnings||[]).map(w=>`<div class="warning">${escape(w.message)}</div>`).join('')}</div></div>`).join(''):'검수 결과가 없습니다.';
}
function updatePredicates(preferred){
  const entity=$('#entity-select').value;
  const reqs=(state.version?.ir?.scenes||[]).flatMap(s=>s.requirements||[]).filter(r=>r.entity_key===entity);
  const predicates=[...new Set(reqs.map(r=>r.predicate))];
  const sel=$('#predicate-select');sel.innerHTML='<option value="">속성 선택</option>'+predicates.map(p=>`<option value="${escape(p)}">${escape(p)}</option>`).join('');
  if(predicates.includes(preferred))sel.value=preferred;
}
async function loadVersion(id, opts={}) {
  const v=await api('GET',`/api/versions/${encodeURIComponent(id)}`);
  state.version=v; state.checks=null;
  try { state.project=await api('GET',`/api/projects/${encodeURIComponent(v.project_id)}`); } catch { /* version view remains usable */ }
  render();
  if(opts.notify!==false)notify(`버전 ${id} 상태: ${statusName(v.state)}`,'success');
  return v;
}
async function safe(fn){try{setBusy(true);await fn()}catch(e){notify(e.message,'error')}finally{setBusy(false);render()}}
$('#create-form').addEventListener('submit',e=>{e.preventDefault();safe(async()=>{
  const form=new FormData(e.currentTarget), seconds=Number(form.get('duration'));
  const created=await api('POST','/api/projects',{title:String(form.get('title')).trim(),prompt:String(form.get('prompt')).trim(),target_duration_ms:seconds?Math.round(seconds*1000):null,reference_images:[]});
  state.project=created;state.version=null;state.checks=null;render();
  await loadVersion(created.version_id,{notify:false});
  notify('프로젝트 접수 완료. 구조화 중이면 잠시 뒤 상태 새로고침을 눌러주세요.','success');
})});
$('#open-button').addEventListener('click',()=>safe(async()=>{const id=$('#open-id').value.trim();if(!id)throw new Error('Version ID를 입력하세요.');await loadVersion(id)}));
$('#refresh-button').addEventListener('click',()=>safe(async()=>{if(state.version)await loadVersion(state.version.version_id)}));
$('#generate-button').addEventListener('click',()=>safe(async()=>{if(!state.version||state.version.state!=='DRAFT')throw new Error('DRAFT 버전에서만 생성할 수 있습니다.');const r=await api('POST',`/api/versions/${encodeURIComponent(state.version.version_id)}/generations`);await loadVersion(r.version_id,{notify:false});notify('생성 요청을 접수했습니다. 상태 새로고침으로 진행 상황을 확인하세요.','success')}));
$('#checks-button').addEventListener('click',()=>safe(async()=>{if(!state.version)return;state.checks=await api('GET',`/api/versions/${encodeURIComponent(state.version.version_id)}/checks`);notify('검수 결과를 불러왔습니다.','success')}));
$('#entity-select').addEventListener('change',()=>updatePredicates());
$('#revision-form').addEventListener('submit',e=>{e.preventDefault();safe(async()=>{
  if(!state.version)throw new Error('먼저 프로젝트 버전을 불러오세요.');
  const f=new FormData(e.currentTarget),scope=String(f.get('scope'));
  const scene=$('#scene-select').value;
  if(scope==='SCENES'&&!scene)throw new Error('선택한 장면을 지정하세요.');
  const body={entity_key:String(f.get('entity_key')),predicate:String(f.get('predicate')),new_value:String(f.get('new_value')).trim(),scope,scene_keys:scope==='SCENES'?[scene]:[]};
  const result=await api('POST',`/api/versions/${encodeURIComponent(state.version.version_id)}/revisions`,body);
  $('#revision-result').innerHTML=`<div class="result"><strong>새 수정 버전이 생성되었습니다.</strong><p>영향받는 장면: ${escape(result.changed_scene_keys.join(', ')||'없음')}</p><small>이전 버전 ${escape(result.parent_version_id)}는 보존됩니다.</small></div>`;
  await loadVersion(result.version_id,{notify:false});notify('선택적 수정 버전(DRAFT)을 생성했습니다. 필요하면 영상 생성 버튼을 누르세요.','success');
})});
(async()=>{try{const data=await api('GET','/health');$('#connection').textContent=data.status==='ok'?'API 연결됨':'API 응답 확인';$('#connection').classList.add('online');await initSetup({health:data,api,notify,escape})}catch{$('#connection').textContent='API 미연결';$('#connection').classList.add('offline');notify('백엔드가 실행되지 않았습니다. README의 FastAPI 실행 방법을 확인하세요.','error')}render()})();
