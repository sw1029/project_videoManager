"""자원별 라우터: 주소와 유스케이스를 잇는다.

밖에서 쓰는 이름 (모듈) - 저마다 router 를 가진다. presentation/app.py 가 앱에 붙인다.
    projects      /api/projects - 프로젝트 만들기, 조회, 대시보드
    versions      /api/versions/{version_id} - 버전 조회, 영상 · 클립 · 참조 이미지 내려받기, 생성 접수,
                  수정 버전, 변경 계획, 검수 · 추적 · 비교 조회
    reference_images    /api/reference-images - 참조 이미지 올리기 (프로젝트를 만들 때 쓴다)
    settings      /api/settings/llm-key - 화면에서 키를 넣고 빼기 (시연 · 로컬 실행용)
    vocabulary    /api/vocabulary/predicates - 술어 어휘
"""

from . import projects, reference_images, settings, versions, vocabulary

__all__ = ["projects", "reference_images", "settings", "versions", "vocabulary"]
