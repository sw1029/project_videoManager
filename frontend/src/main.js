import './style.css';

const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const iconPaths = {
  play:'<path d="m8 5 11 7-11 7z"/>', film:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/>',
  spark:'<path d="m12 3 1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4L12 3zM19 17v4M17 19h4M5 16v4M3 18h4"/>',
  upload:'<path d="M12 16V3M7 8l5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  check:'<path d="m5 12 4 4L19 6"/>', bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  chevron:'<path d="m9 18 6-6-6-6"/>', down:'<path d="m6 9 6 6 6-6"/>', sliders:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
  arrow:'<path d="M5 12h14m-7-7 7 7-7 7"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  cup:'<path d="M5 5h12v10a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5V5zM17 7h2a3 3 0 0 1 0 6h-2M4 22h14"/>',
  person:'<circle cx="12" cy="7" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>', image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
  shield:'<path d="M12 2 4 5v6c0 5 3 8 8 11 5-3 8-6 8-11V5z"/><path d="m9 12 2 2 4-4"/>',
  layers:'<path d="m12 2 9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5"/>',
  refresh:'<path d="M20 11a8 8 0 1 0-2.2 5.7M20 4v7h-7"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  link:'<path d="M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 1 0-7-7l-1.2 1.2M14 11a5 5 0 0 0-7.1 0l-2 2a5 5 0 0 0 7 7l1.2-1.2"/>',
  info:'<circle cx="12" cy="12" r="10"/><path d="M12 11v6M12 7h.01"/>',
  alert:'<path d="m12 3 10 18H2zM12 9v5M12 17h.01"/>',
  cross:'<path d="M18 6 6 18M6 6l12 12"/>',
  compare:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16M9 10l-2 2 2 2M15 10l2 2-2 2"/>',
  edit:'<path d="M12 20h9M16 4l4 4-12 12H4v-4z"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9H3z"/>'
};
function ico(name, sz=20) { return `<svg width="${sz}" height="${sz}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name]||iconPaths.info}</svg>`; }
const DEMO_DSL = `scene intro 0-4000 : 바리스타가 빨간 컵을 들어 보인다\nscene closeup 4000-8000 : 컵 클로즈업\nentity barista PERSON\nentity cup PROP\nin intro: barista, cup\nin closeup: cup\ncup.color = red\nbarista holds -> cup @ intro`;
const DEMO_NL = '카페 신제품 숏폼. 장면 1 (4초): 바리스타가 빨간 머그컵을 들어 보인다. 장면 2 (4초): 빨간 머그컵을 클로즈업한다. 장면 3 (4초): 바리스타가 손을 흔든다.';
const state = {health:null,project:null,version:null,parent:null,checks:null,tracks:null,selectedEntity:'',selectedScene:'',scope:'ALL_APPEARANCES',view:'side',busy:false,poll:null,creating:false};
let noticeTimer;
const isVideo = type => String(type||'').startsWith('video/');
const fmt = ms => `${Math.round((Number(ms)||0)/1000*10)/10}초`;
const time = ms => {let n=Math.max(0,Math.round((Number(ms)||0)/1000));return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;};
const pct = n => Number.isFinite(n) ? `${Math.round(n*100)}%` : '—';
const urlFor = path => path?.startsWith('/') ? path : null;
async function api(method,path,body){
  const resp=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  const json=await resp.json().catch(()=>null);
  if(!resp.ok)throw new Error(json?.error_message||json?.detail?.message||(typeof json?.detail==='string'?json.detail:null)||`HTTP ${resp.status}`);
  return json;
}
function toast(message,kind='ok'){
  const n=$('#notice');n.className='notice '+kind;n.textContent=message;n.hidden=false;
  clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>n.hidden=true,6500);
}
function busy(v){state.busy=v;document.querySelectorAll('button[data-action]').forEach(b=>{if(v)b.disabled=true;});if(!v)render();}
function currentScenes(){return state.version?.ir?.scenes||[];}
function currentEntities(){return state.version?.ir?.entities||[];}
function selectedEntity(){return currentEntities().find(e=>e.entity_key===state.selectedEntity);}
function selectedRequirements(){return currentScenes().flatMap(s=>s.requirements||[]).filter(r=>r.entity_key===state.selectedEntity);}
function durationMs(){return Math.max(1,...currentScenes().map(s=>s.planned_end_ms||0));}
function mediaBox(v,label,type='video'){
  const src= type==='video' ? v?.video_url : null;
  const mediaType=type==='video'?v?.video_media_type:null;
  if(!v && state.version)return `<div class="media missing"><div class="empty-media-icon">${ico('compare',34)}</div><strong>비교할 이전 버전 없음</strong><span>수정 버전을 생성하면 원본과 비교할 수 있습니다.</span></div>`;
  if(src&&isVideo(mediaType))return `<div class="media"><video controls preload="metadata" playsinline src="${esc(urlFor(src)||'')}"></video><span class="media-label">${esc(label)}</span></div>`;
  if(!state.version){const img=label==='수정 후'?'/demo-after.webp':'/demo-before.webp';return `<div class="media sample"><img alt="예시: 카페의 머그컵 색상 변경" src="${img}"/><span class="media-label">${esc(label)}</span><span class="sample-label">디자인 예시 · 실제 결과 아님</span></div>`;}
  return `<div class="media missing"><div class="empty-media-icon">${ico('film',34)}</div><strong>${esc(label)} 영상 없음</strong><span>${v?.state==='COMPLETED'&&mediaType==='application/json'?'현재 Fake 생성 결과는 영상이 아닌 JSON입니다.':v?.state==='FAILED'?'생성 작업이 실패했습니다.':'실제 영상 생성 후 이곳에 표시됩니다.'}</span><span class="tiny">상태: ${esc(v?.state||'아직 생성 전')}</span></div>`;
}
function mainMarkup(){return `
<header class="topbar"><div class="top-left"><div class="brand"><span class="brand-mark">◩</span> FRAME</div><span class="top-divider"></span><button class="project-selector" id="project-action">${ico('folder',17)} <span>내 프로젝트</span> ${ico('down',14)}</button><span class="project-name" id="project-name">카페 신제품 홍보 숏폼</span></div><div class="top-right"><span id="connection" class="connection">${ico('clock',15)} 서버 확인 중</span><button class="guide" id="guide-btn">${ico('info',17)} 사용 가이드</button><span class="avatar" title="FRAME">F</span></div></header>
<main class="workspace">
 <div class="steps">${[['file','요구사항'],['film','생성'],['spark','검수 · 수정'],['upload','출력']].map(([ic,tx],i)=>`<div class="step ${i===2?'active':''}">${ico(ic,21)} <b class="step-num">${i+1}</b><span>${tx}</span></div>${i<3?`<span class="step-arrow">${ico('chevron',17)}</span>`:''}`).join('')}</div>
 <section class="review-panel" id="review">
 <div class="review-header"><div class="heading-left"><span class="heading-icon">${ico('spark',25)}</span><div><h1>검수 · 수정</h1><p>생성된 영상을 확인하고, 원하는 부분만 선택해 수정해 보세요.</p></div></div><div class="view-toggle"><button class="tab selected" data-view="side">${ico('compare',17)} 전후 비교</button><button class="tab" data-view="focus">${ico('film',17)} 크게 보기</button></div></div>
 <div class="compare-grid"><aside class="change-card"><div class="section-kicker">${ico('layers',19)} 적용된 수정 사항</div><div id="change-summary" class="change-summary"><span class="mini-cup">${ico('cup',22)}</span><div><b>컵 색상 변경</b><small>빨간색 → 청록색</small></div>${ico('check',17)}</div><div class="hint-card"><span>${ico('info',19)} 수정 안내</span><p>영상에서 바꾸고 싶은 대상과 구간을 선택하세요. 수정되지 않는 요소는 보존하도록 요청할 수 있습니다.</p></div></aside>
 <div class="viewer ${state.view==='focus'?'single':''}" id="viewer"><div class="video-grid" id="video-grid"></div><div class="change-bar" id="change-bar">${ico('cup',20)} <b>변경 내용</b><span>컵 색상 변경: 빨간색 → 청록색</span><em>화면 디자인 예시</em></div></div></div>
 <section class="timeline-card"><div class="timeline-head"><h2>${ico('film',20)} 객체 타임라인</h2><p>요소가 등장하는 장면을 확인하고 수정할 범위를 선택하세요.</p><span id="timeline-status" class="subtle-badge">장면 기준</span></div><div id="timeline-content" class="timeline-body"></div></section>
 </section>
 <section class="edit-panel" id="edit"><div class="edit-col target"><h2>${ico('sliders',21)} 수정할 대상</h2><p>변경할 객체를 선택하세요.</p><div id="entity-chips" class="entity-chips"></div><label class="field-label" for="scene-select">장면 범위</label><select id="scene-select"></select><label class="checkline"><input id="all-appearances" type="checkbox" checked/> 모든 등장 장면에 적용</label></div>
 <div class="edit-col request"><h2>${ico('edit',21)} 변경 요청</h2><p>변경할 속성과 새 값을 선택하세요.</p><div class="two-col"><label>속성<select id="predicate-select"></select></label><label>새 값<input id="new-value" placeholder="예: blue, teal" maxlength="300"/></label></div><label class="field-label" for="request-text">요청 설명 (참고용)</label><textarea id="request-text" rows="3" placeholder="컵 색상을 청록색으로 바꾸고 배경은 유지해 주세요."></textarea><small class="field-note">현재 API에는 객체·속성·새 값만 전달됩니다. 자유형 요청과 보존 체크는 향후 백엔드 지원이 필요합니다.</small></div>
 <div class="edit-col protect"><h2>${ico('shield',21)} 유지할 요소</h2><p>변경되지 않아야 할 조건</p><label class="protect-item"><input type="checkbox" checked disabled/> 인물 · 동작 <small>보존 요청 UI</small></label><label class="protect-item"><input type="checkbox" checked disabled/> 배경 · 조명 <small>보존 요청 UI</small></label><label class="protect-item"><input type="checkbox" checked disabled/> 컵 형태 · 크기 <small>보존 요청 UI</small></label><small class="field-note">이 체크 항목은 아직 API 요청에 반영되지 않습니다.</small></div>
 <div class="edit-col run"><h2>${ico('spark',21)} 실행</h2><p>원본은 보존하고 새 수정 버전을 생성합니다.</p><button id="revise-btn" class="primary large" data-action="revise">${ico('spark',20)} 수정 버전 만들기</button><button id="generate-btn" class="outline" data-action="generate">${ico('play',18)} 영상 생성 요청</button><span class="run-note">실제 부분 영상 재생성은 생성 모델의 지원이 필요합니다.</span></div></section>
 <section class="secondary-panel" id="project-tools"><div class="panel-section"><h2>${ico('file',21)} 새 프로젝트 · 요구사항</h2><p>프로젝트 생성 후 구조화된 장면을 확인할 수 있습니다.</p><form id="create-form"><div class="two-col"><label>프로젝트 제목<input id="title-input" required value="카페 신제품 홍보 숏폼"/></label><label>목표 길이 (초)<input id="duration-input" type="number" min="1" max="300" value="8"/></label></div><label class="input-label">영상 요구사항<textarea id="prompt-input" rows="5" required></textarea></label><div class="form-actions"><span id="input-mode" class="subtle-badge">서버 확인 중</span><button type="submit" data-action="create" class="primary">${ico('spark',17)} 요구사항 분석 시작</button></div></form></div>
 <div class="panel-section"><h2>${ico('layers',21)} 현재 프로젝트 · 버전</h2><div class="id-block"><span>PROJECT ID</span><b id="project-id">—</b><span>VERSION ID</span><b id="version-id">—</b></div><div class="form-actions"><input id="version-input" placeholder="기존 Version ID" aria-label="기존 버전 ID"/><button id="load-btn" data-action="load" class="outline small">불러오기</button></div><div class="form-actions"><span id="status-pill" class="status-pill">프로젝트 없음</span><button id="refresh-btn" data-action="refresh" class="outline small">${ico('refresh',16)} 새로고침</button></div><div id="scene-list" class="scene-list">생성한 장면이 여기에 표시됩니다.</div></div>
 <div class="panel-section"><h2>${ico('shield',21)} 품질 검수</h2><p>실제 서버의 검수 결과를 조회합니다.</p><div class="metric-grid"><div><span>요구사항 반영률</span><b id="metric-reflection">—</b></div><div><span>일관성</span><b id="metric-consistency">—</b></div><div><span>비대상 보존율</span><b id="metric-preservation">—</b></div></div><button class="outline small" data-action="checks" id="checks-btn">${ico('refresh',16)} 검수 결과 불러오기</button><div id="checks-list" class="checks-list">검수 데이터 없음</div><small class="field-note">Fake 생성·검수 환경의 점수는 실제 영상 성능을 의미하지 않습니다.</small></div></section>
 <footer>FRAME · AI 숏폼 선택적 편집 · 현재 시스템의 실제 기능과 디자인 예시는 구분해서 표시됩니다.</footer>
</main><div id="notice" class="notice" hidden role="status"></div><dialog id="guide-modal"><h2>FRAME 사용 가이드</h2><p>① 아래의 새 프로젝트 영역에서 요구사항을 입력합니다.</p><p>② 구조화 결과와 장면을 확인한 다음 생성 요청을 진행합니다.</p><p>③ 수정할 객체·속성·새 값을 선택하면 원본을 보존한 새 버전을 만듭니다.</p><p>④ Fake 생성기에서는 영상 대신 JSON 결과가 만들어집니다. 실제 영상은 연결된 모델이 있어야 표시됩니다.</p><button id="close-guide" class="primary">확인</button></dialog>`;}
$('#app').innerHTML=mainMarkup();
function setInputMode(){const mode=state.health?.structuring_input;$('#input-mode').textContent=mode==='natural_language'?'자연어 분석 모드':mode==='dsl'?'DSL 형식 입력 모드':'서버 미연결 · DSL 예시';$('#prompt-input').value=mode==='natural_language'?DEMO_NL:DEMO_DSL;$('#duration-input').value=mode==='natural_language'?'12':'8';}
function selectEntity(key){state.selectedEntity=key;state.tracks=null;render();loadTracks().catch(()=>{});}
function render(){
  const v=state.version, scenes=currentScenes(), ents=currentEntities();
  $('#project-name').textContent=state.project?.title||$('#title-input').value||'새 영상 프로젝트';
  $('#project-id').textContent=v?.project_id||state.project?.project_id||'—';
  $('#version-id').textContent=v?.version_id||state.project?.version_id||'—';
  $('#version-input').value= v?.version_id||$('#version-input').value;
  $('#status-pill').textContent=v?.state||state.project?.state||'프로젝트 없음';$('#status-pill').dataset.state=v?.state||'';
  if(ents.length && !ents.some(e=>e.entity_key===state.selectedEntity))state.selectedEntity=ents.find(e=>e.kind==='PROP')?.entity_key||ents[0].entity_key;
  if(scenes.length && !scenes.some(s=>s.scene_key===state.selectedScene))state.selectedScene=scenes[0].scene_key;
  $('#entity-chips').innerHTML=ents.length?ents.map(e=>`<button class="entity-chip ${e.entity_key===state.selectedEntity?'chosen':''}" data-entity="${esc(e.entity_key)}">${ico(e.kind==='PERSON'?'person':e.kind==='PROP'?'cup':'image',17)} ${esc(e.entity_key)}</button>`).join(''):'<span class="empty-small">프로젝트 생성 후 객체를 선택할 수 있습니다.</span>';
  $('#scene-select').innerHTML=scenes.map(s=>`<option value="${esc(s.scene_key)}">${esc(s.scene_key)} · ${time(s.planned_start_ms)} ~ ${time(s.planned_end_ms)}</option>`).join('')||'<option value="">장면 없음</option>';
  $('#scene-select').value=state.selectedScene;
  const reqs=selectedRequirements(),predicates=[...new Set(reqs.map(r=>r.predicate))];
  const prevPred=$('#predicate-select').value;
  $('#predicate-select').innerHTML=predicates.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('')||'<option value="">속성 없음</option>';
  if(predicates.includes(prevPred))$('#predicate-select').value=prevPred;
  $('#all-appearances').checked=state.scope==='ALL_APPEARANCES';
  $('#video-grid').innerHTML=mediaBox(state.parent,'수정 전')+`<span class="between-arrow">${ico('arrow',20)}</span>`+mediaBox(v,'수정 후');
  $('#change-summary').innerHTML=v?.parent_version_id?`<span class="mini-cup">${ico('edit',20)}</span><div><b>선택적 수정 버전</b><small>원본 버전 보존 · 수정 범위 확인</small></div>${ico('check',17)}`:v?`<span class="mini-cup">${ico('film',20)}</span><div><b>초기 생성 버전</b><small>변경 이력이 없습니다.</small></div>`:`<span class="mini-cup">${ico('cup',20)}</span><div><b>컵 색상 변경</b><small>빨간색 → 청록색</small></div>${ico('check',17)}`;
  $('#change-bar').innerHTML=v?`${ico('layers',19)} <b>현재 버전</b><span>${esc(v.version_id.slice(0,13))} · ${esc(v.state)}</span><em>실제 서버 데이터</em>`:`${ico('cup',19)} <b>변경 내용</b><span>컵 색상 변경: 빨간색 → 청록색</span><em>디자인 예시</em>`;
  $('#timeline-content').innerHTML=timelineHTML();
  $('#timeline-status').textContent=state.tracks?.records?.length?'객체 추적 결과 기준':'장면·요구조건 기준';
  $('#scene-list').innerHTML=scenes.length?scenes.map((s,i)=>`<button class="scene-row ${s.scene_key===state.selectedScene?'selected':''}" data-scene="${esc(s.scene_key)}"><b>${i+1}</b><span>${esc(s.description)}</span><small>${time(s.planned_start_ms)}~${time(s.planned_end_ms)}</small></button>`).join(''):'아직 장면 데이터가 없습니다.';
  $('#metric-reflection').textContent=pct(state.checks?.summary?.requirement_reflection_rate);
  $('#metric-consistency').textContent=pct(state.checks?.summary?.consistency_rate);
  $('#metric-preservation').textContent=pct(state.checks?.summary?.preservation_rate);
  $('#checks-list').innerHTML=state.checks?.results?.length?state.checks.results.slice(0,12).map(r=>`<div class="check-row"><span>${esc(r.verdict)}</span><b>${esc(r.check_type)}</b><small>${esc(r.scene_key||'전체')}</small></div>`).join(''):'검수 결과가 없습니다.';
  $('#generate-btn').disabled=state.busy||v?.state!=='DRAFT';
  $('#revise-btn').disabled=state.busy||!v||!ents.length||!predicates.length||v.state==='STRUCTURING'||v.state==='FAILED';
  $('#checks-btn').disabled=state.busy||!v;
  $('#refresh-btn').disabled=state.busy||!v;
  $('#load-btn').disabled=state.busy;
  document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('selected',b.dataset.view===state.view));
  $('#viewer').classList.toggle('single',state.view==='focus');
  if(state.view==='focus')$('#video-grid').firstElementChild?.classList.add('hidden-in-focus');
}
function timelineHTML(){
  let scenes=currentScenes();let d=durationMs();if(!scenes.length)return `<div class="timeline-empty">${ico('film',24)}<b>아직 영상 타임라인이 없습니다</b><p>프로젝트를 생성하면 장면과 객체 정보를 확인할 수 있습니다.</p></div>`;
  let ents=currentEntities().slice(0,5);
  const ticks=Array.from({length:5},(_,i)=>`<span>${time(i*d/4)}</span>`).join('');
  const tracks=state.tracks?.records||[];
  return `<div class="time-axis">${ticks}</div>${ents.map(e=>{
    const matched=tracks.filter(t=>t.entity_key===e.entity_key);
    let regions=[];
    if(matched.length){regions=matched.flatMap(t=>(t.segments||[]).map(seg=>[seg.start_ms,seg.end_ms]));}
    else regions=scenes.filter(s=>s.entity_keys?.includes(e.entity_key)||s.requirements?.some(r=>r.entity_key===e.entity_key)).map(s=>[s.planned_start_ms,s.planned_end_ms]);
    if(!regions.length)return '';
    const color=e.kind==='PERSON'?'person':e.kind==='PROP'?'prop':'background';
    const bars=regions.map(([s,end])=>`<span class="time-bar ${color}" style="left:${Math.min(100,Math.max(0,s/d*100))}%;width:${Math.max(.7,(end-s)/d*100)}%"></span>`).join('');
    return `<div class="timeline-track ${e.entity_key===state.selectedEntity?'selected':''}" data-track="${esc(e.entity_key)}"><span class="track-name">${ico(color==='person'?'person':color==='prop'?'cup':'image',16)} ${esc(e.entity_key)}</span><div class="track-rail">${bars}</div></div>`;
  }).join('')||'<p class="field-note">객체 등장 정보 없음</p>'}<div class="timeline-footer">${ico('info',15)} 하이라이트된 구간은 데이터상 등장 구간이며, 실제 영상의 정밀 추적은 별도 모델이 필요합니다.</div>`;
}
async function refreshVersion(id,{silent=false}={}){
  const v=await api('GET',`/api/versions/${encodeURIComponent(id)}`);
  const previous=state.version;
  state.version=v;
  if(v.parent_version_id&&previous?.version_id===v.parent_version_id)state.parent=previous;
  else if(v.parent_version_id&&state.parent?.version_id!==v.parent_version_id){try{state.parent=await api('GET',`/api/versions/${encodeURIComponent(v.parent_version_id)}`);}catch{state.parent=null;}}
  else if(!v.parent_version_id)state.parent=null;
  state.project=await api('GET',`/api/projects/${encodeURIComponent(v.project_id)}`).catch(()=>state.project);
  render();
  if(!silent)toast(`현재 버전 상태: ${v.state}`);
  if(['STRUCTURING','QUEUED','PROCESSING'].includes(v.state))schedulePoll(v.version_id); else stopPoll();
  if(v.state==='COMPLETED'&&!state.checks)loadChecks().catch(()=>{});
  return v;
}
function stopPoll(){if(state.poll)clearTimeout(state.poll);state.poll=null;}
function schedulePoll(id){stopPoll();state.poll=setTimeout(async()=>{try{if(state.version?.version_id===id){await refreshVersion(id,{silent:true});}}catch(e){stopPoll();toast('상태 자동 갱신에 실패했습니다: '+e.message,'error');}},2500);}
async function loadChecks(){if(!state.version)return;state.checks=await api('GET',`/api/versions/${encodeURIComponent(state.version.version_id)}/checks`);render();}
async function loadTracks(){if(!state.version||!state.selectedEntity)return;const e=selectedEntity();if(!e)return;const id=state.version.version_id;const key=e.entity_key;try{let res=await api('GET',`/api/versions/${encodeURIComponent(id)}/entities/${encodeURIComponent(e.entity_id)}/tracks`);if(state.version?.version_id===id&&state.selectedEntity===key){state.tracks=res;render();}}catch{/* track endpoint may be unavailable before generation */}}
async function run(fn){if(state.busy)return;busy(true);try{await fn()}catch(e){toast(e.message,'error')}finally{busy(false)}}
$('#create-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
  const title=$('#title-input').value.trim(),prompt=$('#prompt-input').value.trim();if(!title||!prompt)throw new Error('제목과 요구사항을 입력하세요.');
  stopPoll();state.checks=null;state.tracks=null;state.parent=null;state.selectedEntity='';state.selectedScene='';
  const created=await api('POST','/api/projects',{title,prompt,target_duration_ms:Math.round(Number($('#duration-input').value)*1000)||undefined,reference_images:[]});
  state.project=created;state.version=null;await refreshVersion(created.version_id,{silent:true});toast('프로젝트가 접수됐습니다. 구조화가 진행 중이면 상태가 자동 갱신됩니다.');
});});
$('#refresh-btn').addEventListener('click',()=>run(async()=>{if(state.version)await refreshVersion(state.version.version_id)}));
$('#load-btn').addEventListener('click',()=>run(async()=>{const id=$('#version-input').value.trim();if(!id)throw new Error('버전 ID를 입력하세요.');state.checks=null;state.tracks=null;await refreshVersion(id);loadTracks();}));
$('#generate-btn').addEventListener('click',()=>run(async()=>{const v=state.version;if(v?.state!=='DRAFT')throw new Error('DRAFT 버전에서만 생성할 수 있습니다.');await api('POST',`/api/versions/${encodeURIComponent(v.version_id)}/generations`);state.checks=null;state.tracks=null;await refreshVersion(v.version_id,{silent:true});toast('생성 요청이 접수되었습니다. 진행 상태가 자동 갱신됩니다.');}));
$('#checks-btn').addEventListener('click',()=>run(async()=>{await loadChecks();toast('검수 결과를 불러왔습니다.');}));
$('#revise-btn').addEventListener('click',()=>run(async()=>{
  const v=state.version;if(!v)throw new Error('먼저 프로젝트를 생성해 주세요.');
  const predicate=$('#predicate-select').value,new_value=$('#new-value').value.trim(),entity_key=state.selectedEntity;
  if(!entity_key||!predicate||!new_value)throw new Error('수정 대상, 속성, 새 값을 모두 입력하세요.');
  const scope=state.scope,scene_keys=scope==='SCENES'?[state.selectedScene]:[];
  if(scope==='SCENES'&&!state.selectedScene)throw new Error('수정할 장면을 선택해 주세요.');
  const r=await api('POST',`/api/versions/${encodeURIComponent(v.version_id)}/revisions`,{entity_key,predicate,new_value,scope,scene_keys});
  state.parent=v;state.checks=null;state.tracks=null;await refreshVersion(r.version_id,{silent:true});toast(`수정 버전 생성 완료. 영향받는 장면: ${r.changed_scene_keys?.join(', ')||'없음'}. 실제 영상은 새 버전에서 생성해야 합니다.`);
}));
$('#entity-chips').addEventListener('click',e=>{const b=e.target.closest('[data-entity]');if(b)selectEntity(b.dataset.entity);});
$('#scene-list').addEventListener('click',e=>{const b=e.target.closest('[data-scene]');if(b){state.selectedScene=b.dataset.scene;render();}});
$('#timeline-content').addEventListener('click',e=>{const b=e.target.closest('[data-track]');if(b)selectEntity(b.dataset.track);});
$('#scene-select').addEventListener('change',e=>state.selectedScene=e.target.value);
$('#all-appearances').addEventListener('change',e=>state.scope=e.target.checked?'ALL_APPEARANCES':'SCENES');
$('.view-toggle').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(b){state.view=b.dataset.view;render();}});
$('#guide-btn').addEventListener('click',()=>$('#guide-modal').showModal());
$('#close-guide').addEventListener('click',()=>$('#guide-modal').close());
$('#project-action').addEventListener('click',()=>$('#project-tools').scrollIntoView({behavior:'smooth'}));
(async()=>{setInputMode();try{state.health=await api('GET','/health');$('#connection').innerHTML=ico('check',15)+' API 연결됨 · '+esc(state.health.adapters?.generator||'');$('#connection').classList.add('connected');setInputMode();}catch{$('#connection').innerHTML=ico('alert',15)+' API 미연결';$('#connection').classList.add('disconnected');}render();})();
