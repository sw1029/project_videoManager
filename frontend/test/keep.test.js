import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

import { ENTITY_ASPECTS, SCENE_ASPECTS, keepId, keepItems, keepPanelHtml, pruneKeep } from '../src/keep.js';

// 서버 계약의 기준: 백엔드가 내보낸 OpenAPI 명세 (python -m frame.tools.export_openapi)
const spec = JSON.parse(await readFile(new URL('../../docs/openapi.json', import.meta.url), 'utf-8'));

const escape = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ENTITIES = [
  { entity_key: 'barista', kind: 'PERSON', display_name: '바리스타' },
  { entity_key: 'cup', kind: 'PROP', display_name: null },
];

test('서버 KeepItemIn 모양으로 보낸다: 장면 요소는 entity_key 없이, 객체 요소는 객체와 함께', () => {
  const selected = new Set([keepId('BACKGROUND'), keepId('SHAPE', 'cup')]);
  assert.deepEqual(keepItems(selected), [
    { aspect: 'BACKGROUND', entity_key: null },
    { aspect: 'SHAPE', entity_key: 'cup' },
  ]);
  assert.deepEqual(keepItems(new Set()), []);
});

test('유지 요소 이름은 서버 계약(KeepItemIn.aspect)과 같다', () => {
  const server = spec.components.schemas.KeepItemIn.properties.aspect.enum;
  const names = [...ENTITY_ASPECTS, ...SCENE_ASPECTS].map(([aspect]) => aspect);
  assert.deepEqual([...names].sort(), [...server].sort());
});

test('다른 버전으로 바뀌어 없어진 객체의 선택은 버린다 (장면 요소는 남긴다)', () => {
  const selected = new Set([keepId('LIGHTING'), keepId('ACTION', 'barista'), keepId('APPEARANCE', 'dog')]);
  assert.deepEqual([...pruneKeep(selected, ENTITIES)], [keepId('LIGHTING'), keepId('ACTION', 'barista')]);
});

test('패널: 장면 전체와 객체마다 체크 상자를 그리고 고른 것은 체크해 둔다', () => {
  const html = keepPanelHtml(ENTITIES, new Set([keepId('ACTION', 'barista')]), escape);
  assert.equal((html.match(/type="checkbox"/g) || []).length, SCENE_ASPECTS.length + ENTITY_ASPECTS.length * ENTITIES.length);
  assert.match(html, /data-keep="ACTION\|barista" checked/);
  assert.match(html, /바리스타/);
  assert.doesNotMatch(html, /data-keep="BACKGROUND" checked/);
});

test('패널: 객체 이름은 이스케이프하고, 객체가 없으면 안내만 보인다', () => {
  const html = keepPanelHtml([{ entity_key: 'cup', display_name: '<b>컵</b>' }], new Set(), escape);
  assert.match(html, /&lt;b&gt;컵&lt;\/b&gt;/);
  const empty = keepPanelHtml([], new Set(), escape);
  assert.doesNotMatch(empty, /checkbox/);
});
