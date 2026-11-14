# 배포판을 이름으로 고정한다 (trixie = Debian 13). 이 이미지의 ffmpeg 는 배포판의 것이라, 태그가 다음 배포판으로 넘어가면
# ffmpeg 의 판도 함께 바뀐다 (감사 A-42 · A-43 이 ffmpeg 의 판 · 빌드 차이에서 났다). 올릴 때는 CI 의 docker 잡이 본다 (ADR-0034)
FROM python:3.11-slim-trixie

# 영상 처리(클립 정규화 · 조립 · 프레임 뽑기 · 화면 문구 합성)는 배포판의 ffmpeg 로 한다 (trixie: 7.1 대).
# video extra 의 번들 ffmpeg 는 넣지 않는다: PATH 의 ffmpeg 가 먼저 쓰여 넣어도 쓰이지 않고,
# 그 번들(Linux 용 7.0.2 정적 빌드)에는 화면 문구를 그리는 drawtext 필터가 없다 (감사 A-43).
# fonts-nanum: 화면 문구에 쓰는 한글 글꼴 (FRAME_OVERLAY_FONT 를 주지 않으면 글꼴 폴더에서 찾는다)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# 서버는 root 가 아닌 사용자로 돈다. /data 에 산출물과 (주소를 정하지 않았을 때의) 파일 DB 를 둔다
RUN useradd --create-home --uid 10001 frame \
    && mkdir -p /data/artifacts /data/db \
    && chown -R frame:frame /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# llm: FRAME_STRUCTURER=llm 과 VLM 검수기·정체성 추출. (ffmpeg 는 위에서 넣었다 - video extra 는 설치하지 않는다)
# cryptography: MySQL 8.4 의 기본 인증(caching_sha2_password)에 pymysql 이 쓴다
RUN pip install --no-cache-dir -e ".[llm]" cryptography

# DB 주소를 정하지 않으면 파일 DB 를 쓴다. in-memory DB 는 프로세스가 끝나면 사라진다
ENV FRAME_PERSISTENCE=sqlalchemy \
    FRAME_DB_URL=sqlite+pysqlite:////data/db/frame.db \
    FRAME_DB_CREATE_SCHEMA=true \
    FRAME_ARTIFACT_STORE=local_fs \
    FRAME_ARTIFACT_DIR=/data/artifacts \
    FRAME_JOBS=thread \
    PYTHONUNBUFFERED=1

USER frame
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]
CMD ["uvicorn", "--factory", "frame.presentation.main:create_default_app", "--host", "0.0.0.0", "--port", "8000"]
