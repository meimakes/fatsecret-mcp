# fatsecret-mcp HTTP/SSE deployment image.
#
# Runs the stdio MCP server behind mcp-proxy so it's reachable as an SSE endpoint.
# Configure via env vars (see README "Deploy on Railway"):
#   FATSECRET_CONSUMER_KEY, FATSECRET_CONSUMER_SECRET,
#   FATSECRET_USER_TOKEN,   FATSECRET_USER_TOKEN_SECRET
#
# Run locally:
#   docker build -t fatsecret-mcp .
#   docker run -p 8000:8000 \
#     -e FATSECRET_CONSUMER_KEY=... \
#     -e FATSECRET_CONSUMER_SECRET=... \
#     -e FATSECRET_USER_TOKEN=... \
#     -e FATSECRET_USER_TOKEN_SECRET=... \
#     fatsecret-mcp
# Endpoint: http://localhost:8000/sse

FROM python:3.12-slim

WORKDIR /app

# Copy the package source so the Dockerfile works from a fresh clone (no PyPI dep).
COPY pyproject.toml README.md LICENSE ./
COPY fatsecret_mcp ./fatsecret_mcp

RUN pip install --no-cache-dir . mcp-proxy

ENV PYTHONUNBUFFERED=1

# Railway / Fly / Cloud Run inject $PORT; default 8000 for local.
EXPOSE 8000

CMD ["sh", "-c", "mcp-proxy --sse-host 0.0.0.0 --sse-port ${PORT:-8000} --pass-environment -- fatsecret-mcp serve"]
