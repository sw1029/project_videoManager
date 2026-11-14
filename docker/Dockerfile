FROM python:3.11-slim

# 서버는 root 가 아닌 사용자로 돈다. /data 에 산출물과 (주소를 정하지 않았을 때의) 파일 DB 를 둔다
RUN useradd --create-home --uid 10001 frame \
    && mkdir -p /data/artifacts /data/db \
    && chown -R frame:frame /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# llm: FRAME_STRUCTURER=llm 과 VLM 검수기·정체성 추출. video: 영상 API 의 클립 정규화와 실제 검수기가 쓰는 ffmpeg.
# cryptography: MySQL 8.4 의 기본 인증(caching_sha2_password)에 pymysql 이 쓴다
RUN pip install --no-cache-dir -e ".[llm,video]" cryptography

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
