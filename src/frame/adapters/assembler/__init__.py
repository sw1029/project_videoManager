"""최종 영상 조립 (VideoAssembler 포트): 장면 클립을 순서대로 이어 영상 하나로 만든다.

맡는 개념
    생성기가 만든 장면 클립을 잇는 일이다. 생성(adapters/generator/)과 다른 포트다 - 수정 버전에서는 다시 만든 장면과
    부모에게서 물려받은 장면의 클립을 함께 잇는다.

밖에서 쓰는 이름
    FfmpegVideoAssembler    ffmpeg concat (FRAME_ASSEMBLER=ffmpeg). 규격이 같으면 스트림 복사, 다르면 다시 인코딩
    FakeVideoAssembler      영상 대신 이은 순서를 적은 결과 정보를 저장한다 (FRAME_ASSEMBLER=fake)

모듈 지도
    ffmpeg · fake    구현 하나가 모듈 하나
"""

from .fake import FakeVideoAssembler
from .ffmpeg import FfmpegVideoAssembler

__all__ = ["FakeVideoAssembler", "FfmpegVideoAssembler"]
