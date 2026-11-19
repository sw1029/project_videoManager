// 진행 막대의 값(0~100)과 현재 단계 설명. 서버가 준 버전 상태와 progress 로만 계산한다.
// progress.stages 는 시작한 단계만 담는다. 그것만으로 비율을 내면 단계가 하나 늘 때마다 비율이 줄어들어,
// 아직 시작하지 않은 단계까지 담은 progress.planned_stages 를 분모로 쓴다.

// 파이프라인을 시작하기 전 상태의 막대 위치. 파이프라인(PROCESSING)은 PIPELINE_START~100 을 단계 진행으로 채운다.
const BEFORE_PIPELINE = Object.freeze({ STRUCTURING: 5, DRAFT: 15, QUEUED: 20 });
const PIPELINE_START = 20;
const PROCESSING_CAP = 99; // 처리 중에는 끝난 것처럼 보이지 않게 한다

// 단계 이름의 화면 표기. 모르는 이름은 그대로 보여 준다.
const STAGE_LABEL = Object.freeze({ generate: '영상 생성', track: '객체 추적', check: '검수' });

const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

function startedStages(progress) {
  return Array.isArray(progress?.stages) ? progress.stages : [];
}

function plannedStages(progress) {
  const planned = progress?.planned_stages;
  return Array.isArray(planned) && planned.length ? planned : startedStages(progress).map((s) => s.stage);
}

function stageRatio(stage) {
  if (!stage) return 0;
  if (stage.status === 'COMPLETED') return 1;
  const total = Number(stage.total) || 0;
  if (total <= 0) return 0; // 항목(장면) 단위 진행이 없는 단계는 끝나야 센다
  return Math.min(1, Math.max(0, (Number(stage.done) || 0) / total));
}

/** 파이프라인 전체에서 끝난 비율 (0~1). 단계마다 같은 몫으로 센다. */
export function pipelineRatio(progress) {
  const planned = plannedStages(progress);
  if (!planned.length) return 0;
  const byName = new Map(startedStages(progress).map((s) => [s.stage, s]));
  return planned.reduce((sum, name) => sum + stageRatio(byName.get(name)), 0) / planned.length;
}

/** 버전 하나의 진행 막대 값 (0~100). 상태가 앞으로 가는 동안 줄어들지 않는다. */
export function progressPercent(version) {
  if (!version) return 0;
  const { state, progress } = version;
  if (state === 'COMPLETED') return 100;
  if (own(BEFORE_PIPELINE, state)) return BEFORE_PIPELINE[state];
  if (!startedStages(progress).length) return state === 'PROCESSING' ? PIPELINE_START : 0;
  const percent = Math.round(PIPELINE_START + (100 - PIPELINE_START) * pipelineRatio(progress));
  return state === 'PROCESSING' ? Math.min(PROCESSING_CAP, percent) : percent;
}

/** 마지막으로 시작한 단계: '객체 추적 (2/3) · 장면 1/3'. 기록이 없으면 빈 문자열. */
export function describeStage(progress) {
  const stages = startedStages(progress);
  const current = stages[stages.length - 1];
  if (!current) return '';
  const planned = Array.isArray(progress.planned_stages) ? progress.planned_stages : [];
  const index = planned.indexOf(current.stage);
  const order = index >= 0 ? ` (${index + 1}/${planned.length})` : '';
  const items = Number(current.total) > 0 ? ` · 장면 ${current.done}/${current.total}` : '';
  const label = own(STAGE_LABEL, current.stage) ? STAGE_LABEL[current.stage] : current.stage;
  return `${label}${order}${items}`;
}
