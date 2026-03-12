FROM python:3.13-slim

ARG APP_VERSION=2.0.0
ARG LEAN_SERVER_LEAN_VERSION=v4.15.0
ARG REPL_REPO_URL=https://github.com/FrederickPu/repl.git
ARG REPL_BRANCH=lean415compat
ARG LEAN_PROJECT_NAME=mathlib4
ARG LEAN_PROJECT_REPO_URL=https://github.com/leanprover-community/mathlib4.git
ARG LEAN_PROJECT_REF=${LEAN_SERVER_LEAN_VERSION}
ARG LEAN_PROJECT_CACHE_CMD="lake exe cache get"
ARG LEAN_PROJECT_UPDATE_MANIFEST=true

LABEL version="${APP_VERSION}"

# Override with docker compose / docker run -e
# NOTE: Do not set LEAN_SERVER_PORT here - Railway provides PORT at runtime
ENV LEAN_SERVER_LEAN_VERSION=${LEAN_SERVER_LEAN_VERSION} \
    LEAN_SERVER_HOST=0.0.0.0 \
    LEAN_SERVER_LOG_LEVEL=INFO \
    LEAN_SERVER_ENVIRONMENT=prod \
    LEAN_SERVER_LEAN_VERSION=${LEAN_SERVER_LEAN_VERSION} \
    LEAN_SERVER_REPL_PATH=/repl/.lake/build/bin/repl \
    LEAN_SERVER_PROJECT_DIR=/${LEAN_PROJECT_NAME} \
    LEAN_SERVER_ENVIRONMENT_ID=mathlib-v4.15 \
    LEAN_SERVER_PROJECT_LABEL=Mathlib \
    LEAN_SERVER_PROJECT_TYPE=mathlib \
    LEAN_SERVER_IDLE_REPL_TTL_SEC=0 \
    LEAN_SERVER_GATEWAY_DEFAULT_ENVIRONMENT=mathlib-v4.15 \
    LEAN_SERVER_GATEWAY_ENVIRONMENTS=[] \
    LEAN_SERVER_GATEWAY_INTERNAL_API_KEY= \
    LEAN_SERVER_MAX_REPLS= \
    LEAN_SERVER_MAX_REPL_USES=-1 \
    LEAN_SERVER_MAX_REPL_MEM=8G \
    LEAN_SERVER_MAX_WAIT=60 \
    LEAN_SERVER_INIT_REPLS={} \
    LEAN_SERVER_DATABASE_URL=
    # LEAN_SERVER_API_KEY is provided at runtime via docker-compose (.env) or -e

RUN apt-get update && apt-get install -y \
      ca-certificates curl git build-essential unzip jq \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY setup.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/setup.sh \
 && LEAN_PROJECT_NAME=${LEAN_PROJECT_NAME} \
    LEAN_PROJECT_REPO_URL=${LEAN_PROJECT_REPO_URL} \
    LEAN_PROJECT_REF=${LEAN_PROJECT_REF} \
    LEAN_PROJECT_CACHE_CMD="${LEAN_PROJECT_CACHE_CMD}" \
    LEAN_PROJECT_UPDATE_MANIFEST=${LEAN_PROJECT_UPDATE_MANIFEST} \
    REPL_REPO_URL=${REPL_REPO_URL} \
    REPL_BRANCH=${REPL_BRANCH} \
    LEAN_SERVER_LEAN_VERSION=${LEAN_SERVER_LEAN_VERSION} \
    /usr/local/bin/setup.sh

ENV PATH=/root/.elan/bin:/root/.local/bin:$PATH

WORKDIR /root/kimina-lean-server

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

COPY server server
COPY client client
COPY prisma prisma
COPY pyproject.toml uv.lock README-client.md ./

RUN uv export --extra server --no-dev --no-emit-project > requirements.txt \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -e . \
 && prisma generate

# Railway provides PORT dynamically at runtime, so EXPOSE is not needed
# EXPOSE 8000


CMD ["python", "-m", "server"]
