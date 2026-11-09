// 시작 시 1회: 서버 상태(/health)에 맞춰 입력 프리셋을 채우고, LLM 모드면 API 키 패널을 보여준다.
import { bindKeyPanel } from './llm-key.js';
import { inputModeLabel, loadPresets, presetsForMode } from './presets.js';

function applyPreset(preset) {
  const form = document.querySelector('#create-form');
  if (!form || !preset) return;
  form.elements.title.value = preset.title;
  form.elements.prompt.value = preset.prompt;
  form.elements.duration.value = String(preset.duration_s);
}

function fillPresetSelect(presets, escape) {
  const select = document.querySelector('#preset-select');
  if (!select) return;
  select.innerHTML = '<option value="">직접 입력</option>'
    + presets.map((p) => `<option value="${escape(p.id)}">${escape(p.label)}</option>`).join('');
  select.addEventListener('change', () => applyPreset(presets.find((p) => p.id === select.value)));
  if (presets.length) select.value = presets[0].id;
}

export async function initSetup({ health, api, notify, escape }) {
  const mode = health?.structuring_input || 'any';
  const modeLabel = document.querySelector('#input-mode');
  if (modeLabel) modeLabel.textContent = inputModeLabel(mode);

  const presets = presetsForMode(await loadPresets(), mode);
  fillPresetSelect(presets, escape);
  applyPreset(presets[0]);

  const panel = document.querySelector('#llm-key-panel');
  if (!panel || !health?.llm_key) return;
  panel.hidden = false;
  bindKeyPanel(panel, { api, notify, escape })(health.llm_key);
  if (!health.llm_key.configured) {
    let message = '서버에 OpenAI API 키가 없습니다. 실행 설정을 확인하세요.';
    if (health.llm_key.runtime_entry) message = 'OpenAI API 키를 입력하면 자연어 요구사항을 분석할 수 있습니다.';
    else if (health.llm_key.runtime_entry_enabled) message = '서버를 실행한 컴퓨터에서 OpenAI API 키를 입력해야 자연어 요구사항을 분석할 수 있습니다.';
    notify(message, 'error');
  }
}
