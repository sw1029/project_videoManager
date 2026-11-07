FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV FRAME_PERSISTENCE=sqlalchemy \
    FRAME_DB_CREATE_SCHEMA=true \
    FRAME_ARTIFACT_STORE=local_fs \
    FRAME_ARTIFACT_DIR=/data/artifacts \
    FRAME_JOBS=thread

EXPOSE 8000
CMD ["uvicorn", "--factory", "frame.presentation.main:create_default_app", "--host", "0.0.0.0", "--port", "8000"]
