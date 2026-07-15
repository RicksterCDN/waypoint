FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.23 /uv /uvx /bin/

# Install dependencies from the frozen lock file first so this layer is cached
# independently of source changes.
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# Copy the rest of the agent (main.py, activity_protocol.py, toolbox.py, agent.yaml, etc.)
COPY . .

EXPOSE 8088

CMD ["uv", "run", "--frozen", "--no-sync", "python", "-m", "waypoint_recorder"]
