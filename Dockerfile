FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir poetry==2.4.1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root


FROM python:3.13-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_ROOT=/app/static

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system olhocao \
    && useradd --system --gid olhocao --home-dir /app olhocao

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --chown=olhocao:olhocao . .

# The media directory is excluded from the build context and mounted as a
# persistent volume at runtime. Creating it here preserves non-root ownership
# when Docker initializes that volume for the first time.
RUN install --directory --owner=olhocao --group=olhocao /app/media

RUN SECRET_KEY=container-build-only-secret-key-with-more-than-fifty-characters \
    DEBUG=off \
    DATABASE_URL=sqlite:////tmp/olhocao-build.sqlite3 \
    STATIC_ROOT=/app/static \
    python manage.py collectstatic --clear --noinput

USER olhocao
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live/', timeout=2)"

CMD ["gunicorn", "olhocao.wsgi:application", "--bind=0.0.0.0:8000", "--no-control-socket", "--access-logfile=-", "--error-logfile=-"]
