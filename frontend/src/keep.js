// 수정 버전에서 '그대로 둘 요소' (서버의 keep, ADR-0017). 서버 KeepItemIn 과 같은 {aspect, entity_key} 를 만든다.
// - 객체 요소(모습·동작·형태)는 객체를 지정하고, 장면 요소(배경·조명·구도)는 객체 없이 보낸다.
// - 바꾸는 속성과 겹치는 요소(예: 컵 색을 바꾸면서 컵의 모습)는 서버가 거절하고 이유를 돌려준다.
//   어느 속성이 어느 요소를 바꾸는지는 서버 규칙이라 여기서 다시 판단하지 않는다.
// 요소 이름이 서버 계약과 같은지는 test/keep.test.js 가 docs/openapi.json 과 맞춰 본다.

export const ENTITY_ASPECTS = Object.freeze([['APPEARANCE', '모습'], ['ACTION', '동작·자세'], ['SHAPE', '형태·크기']]);
export const SCENE_ASPECTS = Object.freeze([['BACKGROUND', '배경'], ['LIGHTING', '조명'], ['COMPOSITION', '구도·카메라']]);

const SEPARATOR = '|'; // 객체 키는 영문·숫자·밑줄이라 겹치지 않는다

/** 체크 상자 하나를 가리키는 값: 장면 요소는 'BACKGROUND', 객체 요소는 'SHAPE|cup'. */
export function keepId(aspect, entityKey = null) {
  return entityKey ? `${aspect}${SEPARATOR}${entityKey}` : aspect;
}

function parse(id) {
  const [aspect, entityKey] = String(id).split(SEPARATOR);
  return { aspect, entity_key: entityKey || null };
}

/** 고른 것(Set of keepId)을 요청 본문의 keep 으로. */
export function keepItems(selected) {
  return [...selected].map(parse);
}

/** 지금 버전에 없는 객체를 가리키는 선택은 버린다 (다른 프로젝트·버전으로 바뀐 경우). */
export function pruneKeep(selected, entities) {
  const keys = new Set((entities || []).map((e) => e.entity_key));
  return new Set([...selected].filter((id) => {
    const { entity_key: key } = parse(id);
    return key === null || keys.has(key);
  }));
}

/** 장면 전체 한 묶음 + 객체마다 한 묶음. selected 에 든 것은 체크해 둔다. */
export function keepPanelHtml(entities, selected, escape) {
  if (!entities?.length) return '<span class="empty-small">프로젝트를 생성하면 그대로 둘 요소를 고를 수 있습니다.</span>';
  const box = (aspect, label, entityKey = null) => {
    const id = keepId(aspect, entityKey);
    return `<label><input type="checkbox" data-keep="${escape(id)}"${selected.has(id) ? ' checked' : ''}/> ${escape(label)}</label>`;
  };
  const group = (title, boxes) => `<div class="keep-group"><b>${title}</b><div class="ux-keep-grid">${boxes}</div></div>`;
  const scene = group('장면 전체', SCENE_ASPECTS.map(([aspect, label]) => box(aspect, label)).join(''));
  const objects = entities.map((e) => {
    const title = e.display_name ? `${escape(e.entity_key)} <small>${escape(e.display_name)}</small>` : escape(e.entity_key);
    return group(title, ENTITY_ASPECTS.map(([aspect, label]) => box(aspect, label, e.entity_key)).join(''));
  }).join('');
  return `<div class="keep-groups">${scene}${objects}</div>`;
}
