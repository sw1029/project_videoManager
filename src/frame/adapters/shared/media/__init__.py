"""영상 처리 도구 (ffmpeg): 어댑터들이 함께 쓰는 실행기.

맡는 개념
    ffmpeg 를 돌리고 그 출력을 읽는 일이다. 생성기의 클립 정규화·최종 영상 조립과, 검수기·정체성 추출기의
    프레임 추출·영상 비교가 같은 실행기를 쓴다.

밖에서 쓰는 이름 — 모듈 경로로 import 한다 (frame.adapters.shared.media.ffmpeg).

모듈 지도
    ffmpeg    run_ffmpeg (명령을 돌려 종료 코드와 출력을 돌려준다), tail (오류 문구에 실을 출력의 끝),
              FfmpegNotRun (실행하지 못했거나 시간 안에 끝나지 않음)
"""

__all__: list[str] = []
