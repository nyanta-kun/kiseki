FROM python:3.12-slim

WORKDIR /app

RUN pip install uv==0.6.10

COPY pyproject.toml .
RUN uv sync --no-dev

COPY . .

# non-root ユーザーで実行（セキュリティベストプラクティス）
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
