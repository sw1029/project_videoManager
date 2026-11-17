/** FRAME UX upgrade. No API tokens in browser storage. Backend remains source of truth. */
import './ux-enhancements.css';
const $ = s => document.querySelector(s);
const escapeHTML = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let ctx;
let activeStep = 0;
let projects = [];
let serverCatalog = false;
let lastProjectId = '';
let lastVersionId = '';
const steps = [
  {name:'요구사항',desc:'영상의 목표와 내용을 설정합니다',anchor:'project-tools'},
  {name:'생성',desc:'장면 구성을 검토하고 영상을 만듭니다',anchor:'viewer'},
  {name:'검수·수정',desc:'검수 결과를 보고 필요한 장면만 수정합니다',anchor:'edit'},
  {name:'출력',desc:'완성된 영상을 확인하고 저장합니다',anchor:'ux-export'},
];
const stateLabel = {STRUCTURING:'요구사항 분석 중',DRAFT:'생성 준비 완료',QUEUED:'작업 대기 중',PROCESSING:'영상 생성·검수 중',COMPLETED:'생성 완료',FAILED:'처리 실패'};
function addElement(parent,position,tag,cl,html){const el=document.createElement(tag);el.className=cl;el.innerHTML=html;parent.insertAdjacentElement(position,el);return el;}
function readHistory(){try {return JSON.parse(localStorage.getItem('frame-recent-projects')||'[]').filter(p=>p?.project_id).slice(0,30);}catch{return []}}
function putHistory(p){if(!p?.project_id)return;const merged=[p,...readHistory().filter(x=>x.project_id!==p.project_id)].slice(0,30);try{localStorage.setItem('frame-recent-projects',JSON.stringify(merged));}catch{/* no storage */}}
export function recordUXProject(p){putHistory({project_id:p.project_id,title:p.title||'제목 없는 프로젝트',version_id:p.version_id,created_at:p.created_at||new Date().toISOString()});refreshProjects().catch(()=>{});}
function renderProjectCards(){const list=$('#ux-project-cards');if(!list)return;const q=($('#ux-project-search')?.value||'').trim().toLowerCase();const filtered=projects.filter(p=>(p.title||'').toLowerCase().includes(q)||p.project_id?.toLowerCase().includes(q));list.innerHTML=filtered.length?filtered.slice(0,40).map(p=>`<button type="button" class="ux-project-card" data-project="${escapeHTML(p.project_id)}"><span class="ux-card-mark">▣</span><span class="ux-project-meta"><strong>${escapeHTML(p.title||'제목 없는 프로젝트')}</strong><small>${escapeHTML(p.latest_state?stateLabel[p.latest_state]||p.latest_state:'프로젝트 열기')} · ${escapeHTML(p.created_at?new Date(p.created_at).toLocaleDateString('ko-KR'):'')}</small></span><span aria-hidden="true">→</span></button>`).join(''):'<div class="ux-empty">프로젝트가 없습니다. 아래에서 새로 만들어 보세요.</div>';$('#ux-project-count').textContent=`${projects.length}개`;$('#ux-project-source').textContent=serverCatalog?'서버 DB에서 조회한 프로젝트':'이 브라우저에서 작업한 프로젝트만 표시 중 (서버 목록 API 없음)';}
async function refreshProjects(){if(!ctx)return;let data=[];try{const res=await ctx.api('GET','/api/projects');if(Array.isArray(res))data=res;else data=res.items||res.projects||[];serverCatalog=true;}catch{serverCatalog=false;data=readHistory();}projects=data;renderProjectCards();}
async function openProject(id){try{const data=await ctx.api('GET',`/api/projects/${encodeURIComponent(id)}`);const versions=data.versions||[];const last=[...versions].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))[0];if(!last?.version_id)throw new Error('이 프로젝트에 열 수 있는 버전이 없습니다.');await ctx.refreshVersion(last.version_id);recordUXProject({project_id:id,title:data.title,version_id:last.version_id});setStep(last.state==='DRAFT'?1:2);}catch(e){ctx.toast('프로젝트 열기 실패: '+e.message,'error')}}
function createStageNav(){const wrap=$('.steps');if(!wrap)return;wrap.classList.add('ux-steps');wrap.innerHTML=steps.map((s,i)=>`<button type="button" class="ux-stage" data-step="${i}" aria-current="${i===0?'step':'false'}"><span class="ux-stage-index">${i+1}</span><span class="ux-stage-text"><b>${s.name}</b><small>${s.desc}</small></span><span class="ux-stage-check" aria-hidden="true">✓</span></button>`).join('');wrap.addEventListener('click',e=>{const b=e.target.closest('[data-step]');if(b)setStep(Number(b.dataset.step));});}
function createLayout(){
  const workspace=$('.workspace');const projectTools=$('#project-tools');const edit=$('#edit');const timeline=$('.timeline-card');const viewer=$('#viewer');
  if(!workspace||!projectTools||!edit||!timeline)return;
  // Timeline is now subordinate to the edit workspace (Premiere/CapCut convention).
  edit.insertAdjacentElement('afterend',timeline);timeline.classList.add('ux-timeline-bottom');
  addElement(workspace,'afterbegin','div','ux-workflow-heading',`<div><p class="ux-kicker">FRAME CREATOR WORKSPACE</p><h2 id="ux-stage-title">01. 영상 요구사항</h2><p id="ux-stage-description">원하는 영상의 내용을 입력하거나 기존 프로젝트에서 이어서 작업하세요.</p></div><div class="ux-connection-hint" id="ux-step-status">시작 전</div>`);
  const bar=addElement(workspace,'afterbegin','section','ux-progress-panel',`<div class="ux-progress-top"><span id="ux-progress-label">프로젝트를 만들면 진행 상황이 표시됩니다</span><strong id="ux-progress-value">0%</strong></div><div class="ux-progress-track" role="progressbar" aria-label="작업 진행률" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div id="ux-progress-fill"></div></div><p id="ux-progress-explain">진행 상태는 서버가 제공한 작업 상태를 기준으로 표시합니다.</p>`);
  // Move heading below workflow nav.
  $('.steps')?.insertAdjacentElement('afterend',bar);
  const projectManager=addElement(projectTools,'afterbegin','section','ux-project-manager',`<div class="ux-title-row"><div><h2>내 프로젝트</h2><p>제목으로 검색하고 최근 작업을 바로 이어서 시작하세요. ID를 직접 입력할 필요가 없습니다.</p></div><span class="ux-bubble" id="ux-project-count">0개</span></div><div class="ux-project-search"><input id="ux-project-search" type="search" placeholder="프로젝트 이름 검색" aria-label="프로젝트 검색"/><button id="ux-project-refresh" type="button">새로고침</button></div><small id="ux-project-source" class="ux-help"></small><div id="ux-project-cards" class="ux-project-cards" aria-live="polite"></div>`);
  $('#ux-project-search').addEventListener('input',renderProjectCards);
  $('#ux-project-refresh').addEventListener('click',()=>refreshProjects().catch(()=>{}));
  $('#ux-project-cards').addEventListener('click',e=>{const b=e.target.closest('[data-project]');if(b)openProject(b.dataset.project);});
  // Technical IDs remain in collapsible advanced details for troubleshooting only.
  const idblock=$('.id-block');if(idblock){let details=document.createElement('details');details.className='ux-advanced';details.hidden=true;details.innerHTML='<summary>고급 정보 · 프로젝트/버전 ID</summary>';idblock.parentElement.insertBefore(details,idblock);details.append(idblock);let load=$('#version-input')?.parentElement;if(load)details.append(load);}
  // Maintenance/keep controls were redundant and inactive in the original prototype.
  edit.querySelector('.edit-col.protect')?.remove();
  // Dedicated generation entrypoint, so generation isn't buried in the edit panel.
  if(viewer){addElement(viewer,'beforebegin','div','ux-generate-cta',`<div><h2>영상 생성</h2><p>장면 구성을 확인한 다음 영상을 생성하세요.</p><small id="ux-generate-hint"></small></div><button id="ux-generate-now" type="button">영상 생성 요청</button>`);$('#ux-generate-now').addEventListener('click',()=>$('#generate-btn')?.click());}
  // Preview ratio control. No stretched video: native aspect ratio is preserved.
  const preview=$('#video-grid');if(preview){const ctrl=addElement(preview,'beforebegin','div','ux-ratio-toolbar',`<label for="ux-ratio-select">미리보기 비율</label><select id="ux-ratio-select"><option value="auto">원본 비율</option><option value="9/16">9:16 · 쇼츠 / 릴스</option><option value="16/9">16:9 · 가로</option><option value="1/1">1:1 · 정사각</option><option value="4/5">4:5 · 세로 피드</option></select><span>원본 영상은 늘리지 않고 여백으로 맞춰 표시합니다.</span>`);$('#ux-ratio-select').addEventListener('change',e=>{$('#video-grid')?.style.setProperty('--ux-aspect',e.target.value==='auto'?'9/16':e.target.value);$('#video-grid')?.classList.toggle('ux-ratio-auto',e.target.value==='auto');});}
  const checks=$('#checks-list');if(checks){addElement(checks,'beforebegin','div','ux-checks-explainer',`<h3>무엇을 검사하나요?</h3><p><strong>요구 반영</strong> 입력한 조건대로 만들어졌는지 · <strong>일관성</strong> 여러 장면에서 동일 객체가 유지되는지 · <strong>비대상 보존</strong> 수정하지 않은 부분이 그대로인지 확인합니다.</p><small>Fake 검수 점수는 실제 영상 품질을 의미하지 않습니다.</small>`);}
  const exportBox=addElement(workspace,'beforeend','section','ux-export',`<div class="ux-title-row"><div><h2>완성 영상 내보내기</h2><p>생성이 완료된 버전을 확인하고 MP4 파일을 저장하세요.</p></div><span id="ux-export-state" class="ux-bubble">결과 없음</span></div><div id="ux-export-message">영상을 생성하면 이곳에서 내보낼 수 있습니다.</div><a id="ux-export-link" class="ux-export-action" hidden download="FRAME-video.mp4">영상 파일 저장</a>`);
  const analysis=addElement(projectTools,'beforeend','section','ux-media-analysis',`<div class="ux-title-row"><div><h2>기존 영상 분석</h2><p>대본·테마·장르 추출을 위한 영상 입력 영역</p></div><span class="ux-bubble">백엔드 연동 대기</span></div><label>분석할 영상 선택<input type="file" id="ux-video-upload" accept="video/*"/></label><div id="ux-media-meta" class="ux-help">선택한 파일은 아직 서버로 전송되지 않습니다.</div><div class="ux-analysis-grid"><article><h3>대본 추출</h3><p>음성을 구간별 텍스트로 변환하는 기능</p><button disabled title="해당 API가 현재 공개되지 않았습니다">API 연결 후 사용 가능</button></article><article><h3>영상 테마·장르 분석</h3><p>장면 구성·출연·분위기 등 공통 특성 추출</p><button disabled title="테마 추출 API와 파일 업로드 계약이 필요합니다">API 연결 후 사용 가능</button></article><article><h3>시리즈 테마 구성</h3><p>영상 간 일관된 출연·스타일·문구 규칙 관리</p><button disabled title="시리즈 관리 API가 필요합니다">API 연결 후 사용 가능</button></article></div>`);
  $('#ux-video-upload').addEventListener('change',e=>{const f=e.target.files?.[0];$('#ux-media-meta').textContent=f?`${f.name} · ${(f.size/1024/1024).toFixed(1)} MB · 로컬 파일 선택됨 (전송하지 않음)`:'파일을 선택하지 않았습니다.';});
  // Existing secure key entry is retained; no duplicate security banner in the main form.

  if($('#request-text')){const note=$('#request-text').parentElement.querySelector('.field-note');if(note)note.textContent='수정 요청을 확인한 뒤 새 영상 버전을 만들 수 있습니다.';}
  const btn=$('#revise-btn');if(btn){btn.insertAdjacentHTML('afterend','<button type="button" id="ux-plan-now" class="ux-outline-action">수정 계획 미리보기</button><p id="ux-plan-info" class="ux-help">실행 전에 영향을 받는 장면과 예상 처리 방식을 확인할 수 있습니다.</p>');$('#ux-plan-now').addEventListener('click',previewRevision);}
  const title=$('#project-tools .panel-section h2');if(title)title.innerHTML='새 영상 만들기';
  const form=$('#create-form');if(form){
    const labels=Array.from(form.querySelectorAll('label'));
    for(const label of labels){
      const text=Array.from(label.childNodes).find(x=>x.nodeType===Node.TEXT_NODE&&x.textContent.trim());
      if(!text)continue;
      const s=text.textContent.trim();
      const naming={'시연 프리셋':'예시 선택','프로젝트 제목':'영상 제목','목표 길이 (초)':'영상 길이 (초)','영상 요구사항':'영상 내용'};
      if(naming[s])text.textContent=naming[s];
    }
    const prompt=$('#prompt-input');if(prompt)prompt.setAttribute('aria-label','만들고 싶은 영상 내용');
    const submit=form.querySelector('button[type=submit]');if(submit)submit.innerHTML='요구사항 분석하기 →';
  }
  createStageNav();createActualPages();setStep(0);refreshProjects().catch(()=>{});
}
const pageRoutes=['requirements','generation','review','export'];
const pageNames={projects:['내 프로젝트','이어서 작업할 프로젝트를 선택하세요.'],requirements:['새 영상 만들기','만들고 싶은 영상의 내용을 입력하세요.'],generation:['영상 생성','장면을 확인하고 영상을 생성하세요.'],review:['검수·수정','영상 결과를 확인하고 필요한 부분만 수정하세요.'],export:['영상 내보내기','완성된 영상을 다운로드하세요.']};
let builtPages=false;
function makePage(host,route){const page=document.createElement('section');page.className='ux-route-page';page.id='ux-page-'+route;page.dataset.route=route;page.setAttribute('aria-label',pageNames[route][0]);page.hidden=true;host.append(page);return page;}
function moveSection(page,node){if(node)page.append(node);}
function pageAction(page,label,route,secondary=false){const row=page.querySelector('.ux-page-actions')||page.appendChild(Object.assign(document.createElement('div'),{className:'ux-page-actions'}));const btn=document.createElement('button');btn.type='button';btn.className=secondary?'ux-page-button secondary':'ux-page-button';btn.textContent=label;btn.addEventListener('click',()=>navigateTo(route));row.append(btn);}
function createActualPages(){
  if(builtPages)return;
  const workspace=$('.workspace'),holder=document.createElement('div');holder.className='ux-page-root';workspace.insertBefore(holder,workspace.querySelector('footer'));
  const pages=Object.fromEntries(['projects',...pageRoutes].map(route=>[route,makePage(holder,route)]));
  const projectTools=$('#project-tools');const columns=Array.from(projectTools.querySelectorAll(':scope > .panel-section'));
  // Each page is a separate route, not a scroll position. Reparenting retains every form, event listener and video element.
  moveSection(pages.projects,$('.ux-project-manager'));
  const projectsHelp=document.createElement('p');projectsHelp.className='ux-page-intro';projectsHelp.textContent='이전에 만들었던 프로젝트를 이름으로 검색해 열 수 있습니다.';pages.projects.prepend(projectsHelp);
  pageAction(pages.projects,'+ 새 프로젝트 만들기','requirements');
  moveSection(pages.requirements,columns[0]);
  const analysis=$('.ux-media-analysis');
  if(analysis){const accordion=document.createElement('details');accordion.className='ux-analysis-disclosure';accordion.innerHTML='<summary><span>기존 영상으로 시작하기</span><small>분석 기능 준비 중</small></summary>';accordion.append(analysis);pages.requirements.append(accordion);}
  pageAction(pages.requirements,'다음: 생성 화면','generation');
  moveSection(pages.generation,columns[1]);
  moveSection(pages.generation,$('.ux-generate-cta'));
  const generationNote=document.createElement('section');generationNote.className='ux-page-instruction';generationNote.innerHTML='<h3>다음 단계</h3><p>영상 생성이 끝나면 검수·수정 화면에서 결과를 확인할 수 있습니다.</p>';
  pages.generation.append(generationNote);
  pageAction(pages.generation,'다음: 검수·수정','review');
  moveSection(pages.review,$('#review'));
  moveSection(pages.review,$('#edit'));
  moveSection(pages.review,$('.timeline-card'));
  moveSection(pages.review,columns[2]);
  const reviewEmpty=document.createElement('section');reviewEmpty.className='ux-empty-state';reviewEmpty.id='ux-review-empty';reviewEmpty.innerHTML='<span class="ux-empty-symbol" aria-hidden="true">◫</span><h2>아직 확인할 영상이 없습니다</h2><p>요구사항을 입력하고 영상을 생성한 다음, 결과를 검수하거나 필요한 부분을 수정할 수 있습니다.</p><button type="button" id="ux-review-back" class="ux-page-button">요구사항 작성하기</button>';pages.review.prepend(reviewEmpty);$('#ux-review-back').addEventListener('click',()=>navigateTo('requirements'));

  pageAction(pages.review,'다음: 출력 화면','export');
  moveSection(pages.export,$('.ux-export'));
  pageAction(pages.export,'검수·수정으로 돌아가기','review',true);
  if(projectTools)projectTools.hidden=true;
  const topName=$('#project-name');if(topName)topName.hidden=true;
  const guide=$('#guide-btn');if(guide)guide.hidden=true;
  const projectAction=$('#project-action');
  if(projectAction){projectAction.setAttribute('aria-label','내 프로젝트 목록 열기');projectAction.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();navigateTo('projects');refreshProjects().catch(()=>{});},true);}
  const brand=$('.brand');if(brand){brand.style.cursor='pointer';brand.title='요구사항 화면으로';brand.addEventListener('click',()=>navigateTo('requirements'));}
  const createForm=$('#create-form');if(createForm)createForm.addEventListener('submit',()=>{});
  // Existing main.js uses #project-tools only for the old scroll handler. It is kept in DOM to avoid breaking listeners.
  window.addEventListener('hashchange',()=>showRouteFromHash());
  builtPages=true;
  showRouteFromHash();
}
function routeFromHash(){const raw=window.location.hash.replace(/^#\/?/,'').split(/[/?]/)[0];return ['projects',...pageRoutes].includes(raw)?raw:'requirements';}
function showRouteFromHash(){
  if(!builtPages)return;
  const route=routeFromHash(),i=pageRoutes.indexOf(route);
  document.querySelectorAll('.ux-route-page').forEach(el=>{el.hidden=el.dataset.route!==route;});
  document.querySelectorAll('.ux-stage').forEach((el,n)=>{const active=n===i;el.classList.toggle('active',active);el.setAttribute('aria-current',active?'step':'false');});
  if(i>=0)activeStep=i;
  const heading=$('#ux-stage-title'),description=$('#ux-stage-description');
  if(heading)heading.textContent=pageNames[route][0];
  if(description)description.textContent=pageNames[route][1];
  const progress=$('.ux-progress-panel');if(progress)progress.hidden=route==='projects'||!ctx?.state?.version;
  const nav=$('.steps');if(nav)nav.hidden=route==='projects';
  window.scrollTo({top:0,behavior:'instant'});
}
function navigateTo(route){if(!['projects',...pageRoutes].includes(route))return;const hash='#/'+route;if(window.location.hash===hash)showRouteFromHash();else window.location.hash=hash;}
function setStep(i){if(pageRoutes[i])navigateTo(pageRoutes[i]);}

function currentProgress(){const v=ctx?.state?.version;const p=v?.progress;const s=p?.stages||[];const total=s.reduce((acc,x)=>acc+(Number(x.total)||0),0);const done=s.reduce((acc,x)=>acc+(Number(x.done)||0),0);if(total>0)return Math.max(1,Math.min(100,Math.round(done/total*100)));return ({STRUCTURING:8,DRAFT:25,QUEUED:35,PROCESSING:55,COMPLETED:100,FAILED:0})[v?.state]||0;}
export function syncUX(){if(!ctx||!$('#ux-progress-label'))return;const v=ctx.state.version;const pct=currentProgress();$('#ux-progress-label').textContent=stateLabel[v?.state]||'프로젝트를 생성하거나 선택하세요';$('#ux-progress-value').textContent=pct+'%';const el=$('#ux-progress-fill');el.style.width=pct+'%';el.parentElement.setAttribute('aria-valuenow',String(pct));const p=v?.progress;$('#ux-progress-explain').textContent=p?.stage?`현재 단계: ${p.stage} · 경과 시간 ${Math.round((p.elapsed_ms||0)/1000)}초`:'수치는 현재 서버 상태를 나타냅니다. 실제 영상 생성 속도의 예측치는 아닙니다.';
  $('#ux-generate-now')?.toggleAttribute('disabled',v?.state!=='DRAFT'||ctx.state.busy);
  const progressBox=$('.ux-progress-panel');if(progressBox)progressBox.hidden=!v||routeFromHash()==='projects';
  const reviewReady=!!v;for(const selector of ['#review','#edit','#ux-page-review > .timeline-card','#ux-page-review > .panel-section']){const el=$(selector);if(el)el.hidden=!reviewReady;}const reviewEmpty=$('#ux-review-empty');if(reviewEmpty)reviewEmpty.hidden=reviewReady;
  const why=v?.state==='DRAFT'?'생성 요청이 가능합니다.':!v?'먼저 프로젝트의 요구사항을 분석하세요.':v.state==='COMPLETED'?'현재 버전은 생성이 완료됐습니다. 수정 버전을 생성하면 다시 요청할 수 있습니다.':v.state==='FAILED'?'이 버전은 실패 상태입니다. 오류를 확인하거나 새 버전을 생성하세요.':'요구사항 분석 또는 생성 작업이 진행 중입니다.';
  $('#ux-generate-hint').textContent=why;
  for(const id of ['generate-btn','revise-btn']){const b=$('#'+id);if(!b)continue;let hint=document.getElementById('ux-reason-'+id);if(!hint){hint=document.createElement('small');hint.className='ux-help';hint.id='ux-reason-'+id;b.insertAdjacentElement('afterend',hint);}hint.textContent=id==='generate-btn'?why:!v?'먼저 프로젝트를 생성하세요.':!ctx.state.selectedEntity?'수정할 객체를 선택하세요.':v.state==='PROCESSING'||v.state==='STRUCTURING'?'처리가 끝나면 수정할 수 있습니다.':'수정 대상과 속성, 새 값을 선택한 뒤 새 버전을 만드세요.';b.title=b.disabled?hint.textContent:'';}
  const video=v?.video_url&&String(v?.video_media_type||'').startsWith('video/');$('#ux-export-state').textContent=v?.state==='COMPLETED'?'완료':stateLabel[v?.state]||'결과 없음';$('#ux-export-message').textContent=video?'실제 영상 파일이 준비됐습니다.':v?.state==='COMPLETED'?'현재는 Fake 영상 생성 결과(JSON)입니다. 실제 MP4를 만들려면 모델 연결이 필요합니다.':'영상이 완료되면 이곳에서 저장할 수 있습니다.';const link=$('#ux-export-link');link.hidden=!video;if(video)link.href=v.video_url;
  const key=ctx.state.project?.project_id;if(key&&key!==lastProjectId){lastProjectId=key;recordUXProject({project_id:key,title:ctx.state.project?.title||$('#project-name')?.textContent,version_id:v?.version_id});}lastVersionId=v?.version_id||'';
  const stats=[['#metric-reflection','요구사항 충족'],['#metric-consistency','장면 간 일관성'],['#metric-preservation','비대상 보존']];for(const [sel,label] of stats){let e=$(sel);if(!e)continue;let cell=e.parentElement;cell.setAttribute('aria-label',`${label} ${e.textContent}`);cell.classList.add('ux-metric-card');}
  if(ctx.state.checks?.results?.length){
    const list=$('#checks-list');const types={REQUIREMENT:'요구사항 반영',CONSISTENCY:'장면 간 일관성',PRESERVATION:'비대상 요소 보존'};
    const rows=ctx.state.checks.results.slice(0,30);
    list.innerHTML=rows.map((r,i)=>`<div class="ux-check-row"><span class="ux-verdict ${escapeHTML(r.verdict)}">${r.verdict==='PASS'?'통과':r.verdict==='FAIL'?'불일치':'확인 필요'}</span><div><strong>${escapeHTML(types[r.check_type]||r.check_type||'검수')}</strong><small>${escapeHTML(r.scene_key||'전체 장면')} · ${escapeHTML(r.entity_key||'장면 공통')}</small></div></div>`).join('');
  }
  document.querySelectorAll('.media video,.media img').forEach(el=>el.style.objectFit='contain');
}
export function collectUXKeep(){return []; /* No hidden preserve controls: backend receives an explicit empty keep list. */}
async function previewRevision(){const v=ctx.state.version;const entity_key=ctx.state.selectedEntity,predicate=$('#predicate-select')?.value,new_value=$('#new-value')?.value.trim();if(!v||!predicate||!new_value){ctx.toast('프로젝트, 수정 대상·속성·새 값을 먼저 선택하세요.','error');return;}const body={entity_key,predicate,new_value,scope:ctx.state.scope,scene_keys:ctx.state.scope==='SCENES'?[ctx.state.selectedScene]:[],keep:collectUXKeep(ctx.state)};try{const plan=await ctx.api('POST',`/api/versions/${encodeURIComponent(v.version_id)}/revision-plans`,body);const affected=(plan.target_scene_keys||[]).join(', ')||'없음';$('#ux-plan-info').textContent=`변경 계획: ${plan.kind||'CHANGE'} · 대상 장면 ${affected}. 실행하면 새 버전이 생성됩니다.`;}catch(e){$('#ux-plan-info').textContent=`변경 계획을 확인하지 못했습니다: ${e.message}`;}}
export function installUX(components){ctx=components;createLayout();syncUX();}
