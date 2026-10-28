FROM python:3.12-slim AS stockfish
ADD --checksum=sha256:9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f \
    https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-linux-x86-64-universal.tar.gz /sf.tgz
RUN tar xzf /sf.tgz -C /tmp && mv /tmp/stockfish/stockfish-linux-x86-64-universal /stockfish-bin

FROM python:3.12-slim
# libgomp1: OpenMP runtime needed by LightGBM.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1     && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
COPY --from=stockfish /stockfish-bin /usr/local/bin/stockfish
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY alembic.ini ./
COPY alembic ./alembic
COPY models ./models
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
