"""시리즈 기능의 어댑터 (ADR-0025~0027): application/ports/series/ 의 구현.

맡는 개념: 포트마다 하위 패키지 하나. 각 패키지의 입구는 테스트·오프라인용 구현(fake · template · memory)만 내보낸다.
실제 구현(ffmpeg · LLM)은 조립(config/theme_adapters.py)이 고를 때 import 하므로, ffmpeg·키 없이도 서버가 뜬다.

밖에서 쓰는 이름: 이 입구는 아무것도 내보내지 않는다. 아래 패키지를 import 한다.

모듈 지도
- source_analysis/   겉모습 읽기 · 컷 찾기 (fake · ffmpeg)
- theme_extraction/  테마 추출 (fake · 프레임을 읽는 LLM)
- planning/          편의 내용 기획 (template · llm)
- audio/             소리 편집 (fake · ffmpeg)
- overlay/           화면 문구 얹기 · 화면의 글자 읽기 (fake · ffmpeg · vlm)
- store/             테마·편 저장 (memory · artifact)

이 패키지 밖에서 함께 쓰는 것 (머지 뒤 adapters/shared/ 로 옮길 것)
- adapters/media_tools.py  ffmpeg 실행 · 영상 정보 읽기 · 영역 SSIM
- adapters/fake_media.py   가짜 영상·음성 형식. 여기의 가짜 구현들과 adapters/speech 의 가짜 인식기가 함께 읽는다
음성 인식 어댑터는 adapters/speech/ 에 있다 (이 기능 밖에서도 쓰는 개념).
"""

__all__: list[str] = []
