"""작업 디스패처 (JobDispatcher 포트, ADR-0005): 접수한 작업(구조화 · 생성 파이프라인)을 실행으로 넘긴다.

맡는 개념
    세 구현 모두 작업을 이 프로세스의 메모리에만 둔다. 프로세스가 죽으면 작업은 사라지고, 재시작 복구가 남은 버전을
    다시 넣는다 (ADR-0013).

밖에서 쓰는 이름
    InlineJobDispatcher        요청 스레드에서 바로 실행한다 (FRAME_JOBS=inline, 시연 런처)
    ThreadPoolJobDispatcher    스레드 풀에서 실행한다 (FRAME_JOBS=thread)
    RecordingJobDispatcher     쌓아 두었다가 테스트가 원하는 때 실행한다 (FRAME_JOBS=recording)

모듈 지도
    inline · thread · recording    구현 하나가 모듈 하나
"""

from .inline import InlineJobDispatcher
from .recording import RecordingJobDispatcher
from .thread import ThreadPoolJobDispatcher

__all__ = ["InlineJobDispatcher", "RecordingJobDispatcher", "ThreadPoolJobDispatcher"]
