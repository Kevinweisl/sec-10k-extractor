FROM python:3.12-slim

# Minimal OS deps. edgartools + lxml use the system libxml2/libxslt that ship
# with python:3.12-slim; if a wheel build is needed for any transitive dep,
# build-essential gets pulled in at install time and discarded after.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cacheable layer) before copying source.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir -e .

# Copy app assets after deps so source edits don't bust the deps layer.
COPY ui ./ui
COPY evals ./evals

# The `evals/` directory is needed at runtime so api/cache.py can fall back
# to deriving the demo manifest from gold + silver source files when
# `ui/demo_cache/manifest.json` doesn't exist. In production the manifest
# WILL exist (built by scripts/build_demo_cache.py before container build),
# but the fallback keeps things working in degraded states.

# Zeabur injects $PORT at runtime; default 8000 for local docker run.
ENV PORT=8000
EXPOSE 8000

# SEC_USER_AGENT is required at startup; supply via Zeabur environment vars.
# EXTRACT_RPM and EXTRACT_TIMEOUT_S have sane defaults; override only if needed.

CMD ["sh", "-c", "python -m uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --app-dir src"]
