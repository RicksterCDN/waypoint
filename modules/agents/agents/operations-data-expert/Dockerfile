FROM python:3.12-slim

WORKDIR /app

# Microsoft ODBC Driver 18 for SQL Server — required by the headless FabricIQ
# path (pyodbc reads the mirrored operational core over the Fabric SQL analytics
# endpoint with a project-MI AAD token). unixodbc is the runtime Driver Manager;
# unixodbc-dev is needed to build/link pyodbc; curl/gnupg to add Microsoft's apt
# repo.
#
# NOTE: we deliberately do NOT `apt-get purge --auto-remove` here. That step
# previously stripped the msodbcsql18 runtime library: odbcinst.ini kept the
# driver registration pointing at
# /opt/microsoft/msodbcsql18/lib64/libmsodbcsql-*.so, but the .so file itself was
# removed, so pyodbc.connect failed at runtime with
# "[unixODBC][Driver Manager]Can't open lib ... : file not found". The build now
# asserts the driver library is physically present so a regression fails loudly
# at build time instead of silently degrading grounding at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg apt-transport-https ca-certificates unixodbc unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && ldconfig \
    && ls -l /opt/microsoft/msodbcsql18/lib64/ \
    && test -n "$(find /opt/microsoft/msodbcsql18/lib64 -name 'libmsodbcsql-18*.so*' -print -quit)" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.23 /uv /uvx /bin/

# Install dependencies from the frozen lock file first so this layer is cached
# independently of source changes.
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# Copy the rest of the agent (main.py, activity_protocol.py, toolbox.py, agent.yaml, etc.)
COPY . .

EXPOSE 8088

CMD ["uv", "run", "--frozen", "--no-sync", "python", "-m", "my_agent"]
