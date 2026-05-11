FROM python:3.13-slim

ARG APP_VERSION=2.0.0
ARG LEAN_SERVER_LEAN_VERSION=v4.9.0
ARG LEAN_SERVER_RUNTIME_IDS=v4.9.0,v4.15.0,v4.24.0,v4.27.0,v4.28.0
ARG LEAN_SERVER_PREINSTALL_RUNTIME_IDS=${LEAN_SERVER_LEAN_VERSION}
ARG RUNTIME_INSTALL_REV=3
ARG REPL_REPO_URL=https://github.com/leanprover-community/repl.git
ARG REPL_BRANCH=${LEAN_SERVER_LEAN_VERSION}
ARG MATHLIB_REPO_URL=https://github.com/leanprover-community/mathlib4.git
ARG MATHLIB_BRANCH=${LEAN_SERVER_LEAN_VERSION}

LABEL version="${APP_VERSION}"

# Override with docker compose / docker run -e
# NOTE: Do not set LEAN_SERVER_PORT here - Railway provides PORT at runtime
ENV LEAN_SERVER_LEAN_VERSION=${LEAN_SERVER_LEAN_VERSION} \
    LEAN_SERVER_RUNTIME_IDS=${LEAN_SERVER_RUNTIME_IDS} \
    LEAN_SERVER_MULTI_RUNTIME_ENABLED=true \
    LEAN_SERVER_HOST=0.0.0.0 \
    LEAN_SERVER_LOG_LEVEL=INFO \
    LEAN_SERVER_ENVIRONMENT=prod \
    LEAN_SERVER_LEAN_VERSION=${LEAN_SERVER_LEAN_VERSION} \
    REPL_REPO_URL=${REPL_REPO_URL} \
    REPL_BRANCH=${REPL_BRANCH} \
    MATHLIB_REPO_URL=${MATHLIB_REPO_URL} \
    MATHLIB_BRANCH=${MATHLIB_BRANCH} \
    LEAN_SERVER_REPL_PATH=/repl/.lake/build/bin/repl \
    LEAN_SERVER_PROJECT_DIR=/mathlib4 \
    LEAN_SERVER_RUNTIME_ROOT=/runtimes \
    LEAN_SERVER_MAX_REPLS= \
    LEAN_SERVER_MAX_TOTAL_REPLS= \
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
RUN echo "Runtime install revision ${RUNTIME_INSTALL_REV}" \
 && chmod +x /usr/local/bin/setup.sh \
 && LEAN_SERVER_RUNTIME_IDS="${LEAN_SERVER_PREINSTALL_RUNTIME_IDS}" /usr/local/bin/setup.sh

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
