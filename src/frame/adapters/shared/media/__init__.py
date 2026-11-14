"""영상 처리 도구 (ffmpeg): 어댑터들이 함께 쓰는 찾기·실행과 영상 읽기.

맡는 개념
    ffmpeg 를 찾아 돌리고 그 출력을 읽는 일이다. 생성기의 클립 정규화·최종 영상 조립과, 검수기·정체성 추출기의
    프레임 추출·영상 비교가 같은 실행기를 쓴다. numpy·OpenCV 같은 추가 패키지 없이 ffmpeg 하나로 한다.

밖에서 쓰는 이름 — 모듈 경로로 import 한다 (frame.adapters.shared.media.frames 처럼).

모듈 지도
    ffmpeg        찾기: resolve_ffmpeg (설정 → PATH → imageio-ffmpeg), require_ffmpeg (경로가 꼭 필요한 어댑터용)
                  실행: run_ffmpeg (종료 코드와 출력), run_reading (영상을 읽는 도구용), tail (출력의 끝)
                  오류: FfmpegNotRun (실행하지 못했거나 시간 안에 끝나지 않음), VideoToolError (영상을 읽지 못함)
    frames        프레임 뽑기: extract_frame, 정규화 좌표의 사각형 Box, 뽑은 이미지 Image
    probe         영상 정보: video_size (가로, 세로)
    similarity    두 영상의 구조 유사도: ssim
"""

__all__: list[str] = []
