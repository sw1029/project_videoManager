"""영상 처리 도구 (ffmpeg): 어댑터들이 함께 쓰는 찾기·실행과 영상·소리 읽기, 가짜 미디어 형식.

맡는 개념
    ffmpeg 를 찾아 돌리고 그 출력을 읽는 일이다. 생성기의 클립 정규화·최종 영상 조립과, 검수기·정체성 추출기의
    프레임 추출·영상 비교가 같은 실행기를 쓴다. numpy·OpenCV 같은 추가 패키지 없이 ffmpeg 하나로 한다.
    테스트·오프라인용 가짜 구현들이 함께 읽는 가짜 영상·음성 형식도 여기에 있다.

밖에서 쓰는 이름 — 모듈 경로로 import 한다 (frame.adapters.shared.media.frames 처럼).

모듈 지도
    ffmpeg        찾기: resolve_ffmpeg (설정 → PATH → imageio-ffmpeg), require_ffmpeg (경로가 꼭 필요한 어댑터용)
                  실행: run_ffmpeg (종료 코드와 출력), run_reading (영상을 읽는 도구용), tail (출력의 끝)
                  오류: FfmpegNotRun (실행하지 못했거나 시간 안에 끝나지 않음), VideoToolError (영상을 읽지 못함)
    frames        프레임 뽑기: FrameSampler (같은 클립의 같은 프레임을 다시 뽑지 않는다 - 어댑터는 이것을 쓴다),
                  midpoints (표본 시각), extract_frame, 정규화 좌표의 사각형 Box, 뽑은 이미지 Image
    probe         영상 정보: video_size (가로, 세로), probe_video (코덱 · 크기 · fps · 프레임 수 · 길이)
    similarity    두 영상의 구조 유사도: ssim
    media_tools   시리즈·음성 어댑터가 쓰는 읽기 (ADR-0025·0027): probe_media (영상·소리 스트림의 겉모습),
                  detect_cuts (컷의 시각), detect_silences (무음 구간), region_ssim (화면 한 영역 안팎의 유사도).
                  오류는 MediaToolError. 실행기(run_ffmpeg · tail)와 영상 정보 읽기가 위의 것과 겹친다 —
                  한 벌로 묶는 일이 남았다 (감사 보고서 P-15)
    fake_media    가짜 영상·음성 형식 (실제 미디어 대신 '무엇이 들어 있는지' 를 적은 JSON): fake_video ·
                  fake_recording · decode_fake_media. 가짜 분석기·인식기·소리 편집기·문구 합성기가 함께 읽는다
"""

__all__: list[str] = []
