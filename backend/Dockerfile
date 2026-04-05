FROM python:3.12-slim

WORKDIR /app

RUN pip install uv==0.6.10

COPY pyproject.toml .
RUN uv sync --no-dev

COPY . .

# non-root ユーザーで実行（セキュリティベストプラクティス）
# .cache/uv を事前作成しておかないと read_only: true + tmpfs 構成で再起動時に Permission denied になる
RUN adduser --disabled-password --gecos '' appuser && \
    mkdir -p /home/appuser/.cache/uv && \
    chown -R appuser /app /home/appuser/.cache
USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
