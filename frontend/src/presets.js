// 시연용 입력 프리셋: /presets.json (빌드 후 dist/presets.json) 을 읽고, 실패하면 내장 기본값을 쓴다.
// DOM 에 의존하지 않는 순수 함수만 둔다 (node --test 로 검증).

export const BUILTIN_PRESETS = [
  {
    id: 'cafe-nl',
    mode: 'natural_language',
    label: '카페 신제품 (자연어)',
    title: '카페 신제품 숏폼',
    duration_s: 12,
    prompt: '카페 신제품 숏폼. 장면 1 (4초): 검은 머리의 바리스타가 빨간 머그컵을 들어 보인다. 장면 2 (4초): 빨간 머그컵을 클로즈업한다. 장면 3 (4초): 바리스타가 손을 흔든다.',
  },
  {
    id: 'cafe-dsl',
    mode: 'dsl',
    label: '카페 신제품 (DSL)',
    title: '카페 신제품 숏폼 (DSL)',
    duration_s: 8,
    prompt: 'scene intro 0-4000 : 바리스타가 컵을 들어 보인다\nscene closeup 4000-8000 : 컵 클로즈업\nentity barista PERSON : 바리스타\nentity cup PROP : 빨간 머그컵\nin intro: barista, cup\nin closeup: cup\ncup.color = red\nbarista holds -> cup @ intro',
  },
];

const MODES = new Set(['natural_language', 'dsl']);

export function normalizePresets(data) {
  const list = Array.isArray(data) ? data : data?.presets;
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  return list.filter((p) => {
    const ok = p && typeof p.id === 'string' && p.id && !seen.has(p.id) && MODES.has(p.mode)
      && typeof p.title === 'string' && p.title.trim() && typeof p.prompt === 'string' && p.prompt.trim()
      && Number.isFinite(p.duration_s) && p.duration_s > 0 && p.duration_s <= 300;
    if (ok) seen.add(p.id);
    return ok;
  }).map((p) => ({ ...p, label: p.label || p.title }));
}

// 서버의 구조화 입력 방식(/health 의 structuring_input)에 맞는 프리셋만 고른다. any 면 전부.
export function presetsForMode(presets, mode) {
  if (mode !== 'natural_language' && mode !== 'dsl') return presets;
  const matched = presets.filter((p) => p.mode === mode);
  return matched.length ? matched : presets;
}

export function inputModeLabel(mode) {
  if (mode === 'natural_language') return '자연어 요구사항 (LLM)';
  if (mode === 'dsl') return 'DSL 입력 (규칙 기반)';
  return '요구사항 입력';
}

export async function loadPresets(fetchImpl = globalThis.fetch) {
  try {
    const response = await fetchImpl('/presets.json', { cache: 'no-store' });
    if (!response.ok) return BUILTIN_PRESETS;
    const presets = normalizePresets(await response.json());
    return presets.length ? presets : BUILTIN_PRESETS;
  } catch {
    return BUILTIN_PRESETS;
  }
}
