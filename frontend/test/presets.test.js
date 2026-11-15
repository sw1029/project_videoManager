import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

import { BUILTIN_PRESETS, inputModeLabel, loadPresets, normalizePresets, presetsForMode } from '../src/presets.js';

const shipped = JSON.parse(await readFile(new URL('../public/presets.json', import.meta.url), 'utf-8'));

test('배포용 presets.json 은 모두 유효하고 두 입력 방식을 다 포함한다', () => {
  const presets = normalizePresets(shipped);
  assert.equal(presets.length, shipped.presets.length);
  assert.ok(presets.some((p) => p.mode === 'natural_language'));
  assert.ok(presets.some((p) => p.mode === 'dsl'));
});

test('잘못된 항목과 중복 id 는 걸러진다', () => {
  const presets = normalizePresets({ presets: [
    { id: 'a', mode: 'dsl', title: 't', prompt: 'p', duration_s: 8 },
    { id: 'a', mode: 'dsl', title: 't2', prompt: 'p2', duration_s: 8 },
    { id: 'b', mode: 'video', title: 't', prompt: 'p', duration_s: 8 },
    { id: 'c', mode: 'dsl', title: '', prompt: 'p', duration_s: 8 },
    { id: 'd', mode: 'dsl', title: 't', prompt: 'p', duration_s: 0 },
  ] });
  assert.deepEqual(presets.map((p) => p.id), ['a']);
  assert.equal(presets[0].label, 't');
});

test('서버 입력 방식에 맞는 프리셋만 고르고, 없으면 전부 보여준다', () => {
  const presets = normalizePresets(shipped);
  assert.ok(presetsForMode(presets, 'dsl').every((p) => p.mode === 'dsl'));
  assert.ok(presetsForMode(presets, 'natural_language').every((p) => p.mode === 'natural_language'));
  assert.equal(presetsForMode(presets, 'any').length, presets.length);
  assert.equal(presetsForMode(presets.filter((p) => p.mode === 'dsl'), 'natural_language').length, 2);
});

test('presets.json 을 못 읽으면 내장 기본값을 쓴다', async () => {
  assert.deepEqual(await loadPresets(async () => { throw new Error('offline'); }), BUILTIN_PRESETS);
  assert.deepEqual(await loadPresets(async () => ({ ok: false })), BUILTIN_PRESETS);
  const loaded = await loadPresets(async () => ({ ok: true, json: async () => shipped }));
  assert.equal(loaded.length, shipped.presets.length);
});

test('입력 방식 라벨', () => {
  assert.match(inputModeLabel('natural_language'), /자연어/);
  assert.match(inputModeLabel('dsl'), /DSL/);
  assert.equal(inputModeLabel('any'), '요구사항 입력');
});
