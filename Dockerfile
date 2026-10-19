# The arena server. Every dependency comes from a wheel (manylinux, x86-64 or arm64).
#
#   docker build -t crowdbook-arena .
#   docker run -p 8000:8000 -v arena-data:/data crowdbook-arena
#
# Wheels in plugins/ (a private package of market kinds, say) are installed too, at build time.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY kit ./kit
COPY src ./src
RUN uv sync --locked --no-dev --extra postgres
COPY plugins ./plugins
RUN if ls plugins/*.whl >/dev/null 2>&1; then uv pip install --python /app/.venv plugins/*.whl; fi

RUN useradd --create-home arena && mkdir /data && chown arena /data
USER arena
ENV PATH=/app/.venv/bin:$PATH \
    ARENA_HOST=0.0.0.0 \
    ARENA_PORT=8000 \
    ARENA_DATABASE_URL=sqlite:////data/arena.db \
    ARENA_WORK_DIR=/data/rounds
VOLUME /data
EXPOSE 8000
CMD ["crowdbook-arena"]
