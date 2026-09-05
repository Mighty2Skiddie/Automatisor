# One image, three roles. The MCP server, the API and the Streamlit UI share every
# dependency and all of app/, so building three near-identical images would triple
# build time and pull the same wheels three times for no benefit. docker-compose
# overrides the command per service.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first, so a source edit does not re-resolve ~100 pinned packages.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY evals/ ./evals/
COPY pyproject.toml ./

# Run unprivileged. Nothing here needs root, and the MCP surface is read-only.
RUN useradd --create-home --uid 10001 analyst && chown -R analyst:analyst /app
USER analyst

# Overridden per service in docker-compose.yml.
CMD ["python", "-m", "app.mcp_server.server"]
