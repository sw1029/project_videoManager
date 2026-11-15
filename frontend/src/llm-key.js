// OpenAI API 키 입력란. 키는 브라우저에 저장하지 않고, 이 기기에서 실행 중인 서버 메모리에만 한 번 전달한다.
// 서버가 FRAME_LLM_RUNTIME_KEY=true 이고 이 화면이 서버와 같은 기기에서 열렸을 때만 입력란이 보인다
// (runtime_entry 는 서버가 요청한 기기 기준으로 알려준다. 다른 기기에서 온 입력은 서버가 거부한다).

const KEY_FORMAT = /^sk-[A-Za-z0-9_-]{17,400}$/;
const SOURCE_LABEL = {
  runtime: '화면에서 입력한 키',
  config: '서버 설정 키',
  env: '서버 환경 변수 키 (OPENAI_API_KEY)',
  file: '서버 키 파일',
};

export function isPlausibleKey(value) {
  return KEY_FORMAT.test(String(value ?? '').trim());
}

export function describeKeyStatus(status) {
  if (!status) return 'LLM 구조화를 쓰지 않는 설정입니다.';
  if (status.configured) {
    return `${SOURCE_LABEL[status.source] || '키'} 사용 중 (${status.hint || '***'})`;
  }
  if (status.runtime_entry) return '키가 없습니다. 아래에 OpenAI API 키를 입력하세요.';
  if (status.runtime_entry_enabled) return '키가 없습니다. 키는 서버를 실행한 컴퓨터의 화면에서 입력하세요.';
  return '키가 없습니다. 서버 실행 설정(FRAME_LLM_KEY_FILE 또는 OPENAI_API_KEY)이 필요합니다.';
}

export function keyPanelHtml(status, escape) {
  const statusLine = `<div class="key-status ${status?.configured ? 'ok' : 'missing'}">${escape(describeKeyStatus(status))}</div>`;
  if (!status?.runtime_entry) {
    return `<div class="key-title">OpenAI API 키</div>${statusLine}`;
  }
  return `<div class="key-title">OpenAI API 키</div>${statusLine}`
    + '<div class="key-row"><input id="llm-key-input" type="password" autocomplete="off" spellcheck="false" placeholder="sk-..." aria-label="OpenAI API 키"/>'
    + '<button type="button" class="secondary" id="llm-key-apply">적용</button>'
    + `<button type="button" class="secondary" id="llm-key-clear"${status.source === 'runtime' ? '' : ' disabled'}>입력 키 지우기</button></div>`
    + '<p class="hint">키는 브라우저에 저장하지 않고 이 기기의 서버 메모리에만 둡니다. 서버를 다시 시작하면 다시 입력해야 합니다.</p>';
}

export function bindKeyPanel(panel, { api, notify, escape }) {
  const render = (status) => { panel.innerHTML = keyPanelHtml(status, escape); };
  panel.addEventListener('click', async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !['llm-key-apply', 'llm-key-clear'].includes(target.id)) return;
    const buttons = panel.querySelectorAll('button');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      if (target.id === 'llm-key-apply') {
        const input = panel.querySelector('#llm-key-input');
        const value = input.value.trim();
        if (!isPlausibleKey(value)) throw new Error('API 키 형식이 올바르지 않습니다. sk- 로 시작하는 키를 입력하세요.');
        const status = await api('PUT', '/api/settings/llm-key', { api_key: value });
        input.value = '';
        render(status);
        notify(`API 키를 적용했습니다 (${status.hint}). 이제 자연어 요구사항을 분석할 수 있습니다.`, 'success');
      } else {
        const status = await api('DELETE', '/api/settings/llm-key');
        render(status);
        notify('화면에서 입력한 키를 지웠습니다.', 'success');
      }
    } catch (error) {
      notify(error.message, 'error');
      buttons.forEach((b) => { b.disabled = false; });
    }
  });
  return render;
}
