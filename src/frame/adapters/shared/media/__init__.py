"""영상 처리 도구 (ffmpeg): 어댑터들이 함께 쓰는 찾기·실행과 영상·소리 읽기, 가짜 미디어 형식.

맡는 개념
    ffmpeg 를 찾아 돌리고 그 출력을 읽는 일이다. 생성기의 클립 정규화·최종 영상 조립과, 검수기·정체성 추출기의
    프레임 추출·영상 비교가 같은 실행기를 쓴다. numpy·OpenCV 같은 추가 패키지 없이 ffmpeg 하나로 한다.
    테스트·오프라인용 가짜 구현들이 함께 읽는 가짜 영상·음성 형식도 여기에 있다.

밖에서 쓰는 이름 — 모듈 경로로 import 한다 (frame.adapters.shared.media.frames 처럼).

모듈 지도
    ffmpeg        찾기: resolve_ffmpeg (설정 → PATH → imageio-ffmpeg), require_ffmpeg (경로가 꼭 필요한 어댑터용)
                  실행: run_ffmpeg (종료 코드와 출력), run_reading (영상 · 소리를 읽는 도구용), tail (출력의 끝),
                  한 번에 주는 시간 READ_TIMEOUT_S · WORK_TIMEOUT_S
                  오류: FfmpegNotRun (실행하지 못했거나 시간 안에 끝나지 않음), VideoToolError (영상 · 소리를
                  읽지 못함 - 읽는 도구의 오류는 이것 하나다)
    frames        프레임 뽑기: FrameSampler (같은 클립의 같은 프레임을 다시 뽑지 않는다 - 어댑터는 이것을 쓴다),
                  midpoints (표본 시각), extract_frame, 정규화 좌표의 사각형 Box, 뽑은 이미지 Image
    probe         영상 정보: video_size (가로, 세로),
                  probe_video (첫 영상 스트림의 코덱 · 크기 · fps · 프레임 수 · 길이),
                  probe_media (파일 전체의 겉모습 - 영상 · 소리 스트림. 소리만 있는 파일도 읽는다),
                  read_video_packets (프레임 세기 - 패킷 목록의 줄 수. ffmpeg 의 진행 줄에 기대지 않는다)
    similarity    두 영상의 구조 유사도: ssim (화면 전체), region_ssim (화면 한 영역의 안 또는 밖)
    detection     끝까지 읽어 찾는 것: detect_cuts (컷의 시각), detect_silences (무음 구간)
    fake_media    가짜 영상·음성 형식 (실제 미디어 대신 '무엇이 들어 있는지' 를 적은 JSON): fake_video ·
                  fake_recording · decode_fake_media. 가짜 분석기·인식기·소리 편집기·문구 합성기가 함께 읽는다
"""

__all__: list[str] = []
