import assert from 'node:assert/strict';
import { test } from 'node:test';

import { describeKeyStatus, isPlausibleKey, keyPanelHtml } from '../src/llm-key.js';

const escape = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

test('키 형식 검사', () => {
  assert.ok(isPlausibleKey(`sk-proj-${'a'.repeat(30)}`));
  assert.ok(isPlausibleKey(`  sk-${'b'.repeat(20)}  `));
  assert.ok(!isPlausibleKey('not-a-key'));
  assert.ok(!isPlausibleKey('sk-short'));
  assert.ok(!isPlausibleKey(`sk-${'a'.repeat(20)} b`));
});

test('상태 문구는 출처와 끝 4자리만 보여준다', () => {
  assert.equal(describeKeyStatus({ configured: true, source: 'file', hint: 'sk-...1234', runtime_entry: false }), '서버 키 파일 사용 중 (sk-...1234)');
  assert.match(describeKeyStatus({ configured: false, source: null, hint: null, runtime_entry: true }), /입력하세요/);
  assert.match(describeKeyStatus({ configured: false, source: null, hint: null, runtime_entry: false }), /서버 실행 설정/);
  // 다른 기기(iPad 등)에서 볼 때: 입력은 켜져 있지만 이 기기에서는 못 넣는다
  assert.match(describeKeyStatus({ configured: false, source: null, hint: null, runtime_entry: false, runtime_entry_enabled: true }), /서버를 실행한 컴퓨터/);
  assert.match(describeKeyStatus(null), /쓰지 않는/);
});

test('입력란은 화면 키 입력이 켜졌을 때만 그린다', () => {
  const off = keyPanelHtml({ configured: true, source: 'file', hint: 'sk-...1234', runtime_entry: false }, escape);
  assert.ok(!off.includes('llm-key-input'));
  const remote = keyPanelHtml({ configured: false, source: null, hint: null, runtime_entry: false, runtime_entry_enabled: true }, escape);
  assert.ok(!remote.includes('llm-key-input'));
  const on = keyPanelHtml({ configured: false, source: null, hint: null, runtime_entry: true }, escape);
  assert.ok(on.includes('type="password"') && on.includes('autocomplete="off"'));
  assert.ok(on.includes('id="llm-key-clear" disabled'));
  const runtime = keyPanelHtml({ configured: true, source: 'runtime', hint: 'sk-...WXYZ', runtime_entry: true }, escape);
  assert.ok(!runtime.includes('id="llm-key-clear" disabled'));
});
