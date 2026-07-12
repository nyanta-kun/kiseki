FROM python:3.12-slim

WORKDIR /app

# LightGBM (v26 モデル) requires libgomp1 (OpenMP runtime)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv==0.6.10

# non-root ユーザーで実行（セキュリティベストプラクティス）
# ⚠️ ユーザー作成は COPY より前に行い、以降は COPY --chown で所有権を付ける。
#    COPY 後の `chown -R /app` は .venv 込み約340MBのレイヤ複製を毎ビルド生成し、
#    デプロイ時の docker pull を約6分に肥大化させていた（2026-07-12 修正）。
# .cache/uv を事前作成しておかないと read_only: true + tmpfs 構成で再起動時に Permission denied になる
RUN adduser --disabled-password --gecos '' appuser && \
    mkdir -p /home/appuser/.cache/uv && \
    chown -R appuser /app /home/appuser/.cache
USER appuser

COPY --chown=appuser:appuser pyproject.toml .
RUN uv sync --no-dev

COPY --chown=appuser:appuser . .

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
