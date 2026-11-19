import assert from 'node:assert/strict';
import { test } from 'node:test';

import { describeStage, pipelineRatio, progressPercent } from '../src/progress.js';

const PLANNED = ['generate', 'track', 'check'];
const stage = (name, status, done = 0, total = 0, current = null) => ({ stage: name, status, done, total, current, elapsed_ms: 0 });
const processing = (...stages) => ({ state: 'PROCESSING', progress: { stage: stages.at(-1).stage, elapsed_ms: 0, stages, planned_stages: PLANNED } });

test('단계가 하나씩 늘어도 진행률은 뒤로 가지 않는다 (상태 변화 전 구간)', () => {
  const timeline = [
    { state: 'STRUCTURING', progress: null },
    { state: 'DRAFT', progress: null },
    { state: 'QUEUED', progress: null },
    processing(stage('generate', 'RUNNING', 0, 3)),
    processing(stage('generate', 'RUNNING', 2, 3)),
    processing(stage('generate', 'COMPLETED', 3, 3)),
    processing(stage('generate', 'COMPLETED', 3, 3), stage('track', 'RUNNING', 0, 3)),
    processing(stage('generate', 'COMPLETED', 3, 3), stage('track', 'COMPLETED', 3, 3)),
    processing(stage('generate', 'COMPLETED', 3, 3), stage('track', 'COMPLETED', 3, 3), stage('check', 'RUNNING')),
    { state: 'COMPLETED', progress: { stages: [], planned_stages: PLANNED } },
  ];
  const values = timeline.map(progressPercent);
  values.slice(1).forEach((value, i) => assert.ok(value >= values[i], `${values[i]} -> ${value} (${i + 1}번째)`));
  assert.equal(values.at(-1), 100);
});

test('아직 시작하지 않은 단계도 분모에 넣는다', () => {
  assert.equal(pipelineRatio({ stages: [stage('generate', 'COMPLETED', 3, 3)], planned_stages: PLANNED }), 1 / 3);
  assert.equal(pipelineRatio({ stages: [stage('generate', 'RUNNING', 1, 2)], planned_stages: PLANNED }), 0.5 / 3);
});

test('처리 중에는 모든 단계가 끝나 보여도 100% 로 표시하지 않는다', () => {
  const done = processing(stage('generate', 'COMPLETED', 1, 1), stage('track', 'COMPLETED', 1, 1), stage('check', 'COMPLETED'));
  assert.equal(progressPercent(done), 99);
});

test('실패한 버전은 멈춘 지점을 보여 주고, 단계 기록이 없으면 0', () => {
  const failed = { state: 'FAILED', progress: { stages: [stage('generate', 'FAILED', 1, 3, 'closeup')], planned_stages: PLANNED } };
  const before = progressPercent({ state: 'QUEUED', progress: null });
  assert.ok(progressPercent(failed) > before);
  assert.ok(progressPercent(failed) < 50);
  assert.equal(progressPercent({ state: 'FAILED', progress: null }), 0);
  assert.equal(progressPercent(null), 0);
});

test('planned_stages 가 없는 응답은 시작한 단계만으로 센다', () => {
  assert.equal(pipelineRatio({ stages: [stage('generate', 'COMPLETED', 3, 3)] }), 1);
  assert.equal(pipelineRatio(null), 0);
});

test('현재 단계 설명: 이름 · 순서 · 장면 수 (모르는 단계 이름은 그대로)', () => {
  const p = processing(stage('generate', 'COMPLETED', 3, 3), stage('track', 'RUNNING', 1, 3, 'intro')).progress;
  assert.equal(describeStage(p), '객체 추적 (2/3) · 장면 1/3');
  assert.equal(describeStage({ stages: [stage('check', 'RUNNING')], planned_stages: PLANNED }), '검수 (3/3)');
  assert.equal(describeStage({ stages: [stage('render', 'RUNNING')], planned_stages: [] }), 'render');
  assert.equal(describeStage(null), '');
});
