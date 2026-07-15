import { existsSync } from "node:fs";
import { createServer } from "node:http";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { joinSession, createCanvas, CanvasError } from "@github/copilot-sdk/extension";

const DEFAULT_ENDPOINT = "http://localhost:8088/responses";
const DEFAULT_TITLE = "Responses Chat";
const DEFAULT_PROMPT = "Send a short hello message and summarize what tools you have available.";
const DEFAULT_PORT = 8088;
// A full assurance-orchestrator fan-out (four experts + waypoint-recorder handoff) can take well over
// 120s end to end. Keep this client-side timeout generous and overridable so a
// local full-fan-out run is not aborted mid-flight. (The ~120s cap that the brief
// fights only applies to the *cloud* hosted-agent synchronous bridge; for that,
// drive assurance-orchestrator in background mode via scripts/drive_hosted_agent.py.)
const RESPONSES_TIMEOUT_MS = (() => {
    const raw = process.env.RESPONSES_CHAT_TIMEOUT_MS;
    const parsed = raw ? Number.parseInt(raw, 10) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 240000;
})();
const EXTENSION_DIR = path.dirname(fileURLToPath(import.meta.url));
const RUNNER_STATE_DIR = path.join(os.tmpdir(), "copilot-responses-chat-runners");
const DEFAULT_STARTUP_INSTRUCTIONS = `Start any Forge agent locally from the repo root:

1. If the agent needs local environment variables, create or update its agent-local .env file:
   agents\\<agent-name>\\.env

   Common local values:
   FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
   AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5-mini

2. Start the local Responses API server:
   azd ai agent run <agent-name>

3. Leave that terminal running. The local endpoint is usually:
   http://localhost:8088/responses

4. Point this canvas at that endpoint and send a prompt.

If this agent needs the Waypoint API:

1. Start Waypoint Aspire separately and find the local api resource endpoint.
2. Add the Waypoint settings to the same agent .env file:
   # WAYPOINT_API_BASE_URL=https://localhost:<waypoint-api-port>
   # WAYPOINT_API_SCOPE=
3. Keep WAYPOINT_API_SCOPE empty for local loopback dev auth unless you intentionally enabled MSAL validation.`;
const DEFAULT_VSCODE_INSTRUCTIONS = `Debug a local Forge agent from VS Code:

1. Open this repo folder in VS Code.

2. Set breakpoints in the agent files you want to inspect.
   Example:
   agents\\<agent-name>\\main.py

3. Start the debugpy-backed task from VS Code:
   Terminal > Run Task... > Run <agent-name> with debugpy

4. Start the attach configuration:
   Run and Debug > Attach to <agent-name>

5. Leave the local agent running. Use this canvas against:
   http://localhost:8088/responses

If the agent does not have VS Code tasks yet, add a task that runs:
   azd ai agent run <agent-name> --start-command 'uv run --frozen python -m debugpy --listen 5678 -m <python-module>'

Then add a Python attach launch configuration for localhost:5678.`;
const DEFAULT_QUICK_PROMPTS = [
    {
        label: "Hello",
        prompt: "Send a short hello message and summarize what tools you have available.",
    },
    {
        label: "List tools",
        prompt: "List the tools you have available and what each one is for.",
    },
];

const servers = new Map();

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "responses-chat",
            displayName: "Responses Chat",
            description: "Generic debug chat canvas for testing a local Responses API endpoint.",
            inputSchema: {
                type: "object",
                properties: {
                    endpoint: { type: "string" },
                    title: { type: "string" },
                    prompt: { type: "string" },
                    startupInstructions: { type: "string" },
                    vscodeInstructions: { type: "string" },
                    quickPrompts: {
                        type: "array",
                        items: {
                            type: "object",
                            properties: {
                                label: { type: "string" },
                                prompt: { type: "string" },
                            },
                            required: ["label", "prompt"],
                            additionalProperties: false,
                        },
                    },
                },
                additionalProperties: false,
            },
            actions: [
                {
                    name: "configure",
                    description: "Configure title, endpoint, draft prompt, or quick prompts for this chat canvas.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            endpoint: { type: "string" },
                            title: { type: "string" },
                            prompt: { type: "string" },
                            startupInstructions: { type: "string" },
                            vscodeInstructions: { type: "string" },
                            quickPrompts: {
                                type: "array",
                                items: {
                                    type: "object",
                                    properties: {
                                        label: { type: "string" },
                                        prompt: { type: "string" },
                                    },
                                    required: ["label", "prompt"],
                                    additionalProperties: false,
                                },
                            },
                        },
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        applyConfig(entry.state, ctx.input || {});
                        return publicState(entry.state);
                    },
                },
                {
                    name: "send_message",
                    description: "Send a prompt to the configured local Responses API endpoint.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            message: { type: "string" },
                            endpoint: { type: "string" },
                        },
                        required: ["message"],
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        const endpoint = normalizeEndpoint(ctx.input.endpoint || entry.state.endpoint);
                        const result = await sendToResponses(endpoint, ctx.input.message);
                        appendExchange(entry.state, ctx.input.message, result);
                        return result;
                    },
                },
                {
                    name: "clear_chat",
                    description: "Clear the chat transcript for this canvas instance.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        entry.state.messages = [];
                        entry.state.lastTrace = undefined;
                        entry.state.lastError = undefined;
                        return publicState(entry.state);
                    },
                },
                {
                    name: "get_diagnostics",
                    description: "Return the current canvas diagnostic snapshot without calling the Responses endpoint.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        return buildDiagnostics(entry.state);
                    },
                },
                {
                    name: "get_trace",
                    description: "Return the latest raw Responses API trace captured by this canvas.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        return entry.state.lastTrace || { available: false, message: "No response trace captured yet." };
                    },
                },
                {
                    name: "check_endpoint",
                    description: "Send a small test prompt to the configured Responses endpoint and return diagnostics.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            endpoint: { type: "string" },
                            message: { type: "string" },
                        },
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        return runEndpointDiagnostic(entry.state, ctx.input || {});
                    },
                },
                {
                    name: "send_diagnostics_to_chat",
                    description: "Send the latest canvas diagnostics into the Copilot chat for investigation.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        await sendDiagnosticsToChat(buildDiagnostics(entry.state));
                        return { sent: true, diagnostics: buildDiagnostics(entry.state) };
                    },
                },
                {
                    name: "list_agents",
                    description: "List local Azure AI agent services discovered from azure.yaml.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        entry.state.agents = await discoverAgents();
                        touch(entry.state);
                        return publicState(entry.state);
                    },
                },
                {
                    name: "start_agent",
                    description: "Start a selected local agent with azd ai agent run --no-inspector.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            agentName: { type: "string" },
                            port: { type: "integer" },
                        },
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        await startAgent(entry, ctx.input || {});
                        return publicState(entry.state);
                    },
                },
                {
                    name: "stop_agent",
                    description: "Stop the agent process started by this canvas instance.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        await stopAgent(entry, ctx.instanceId);
                        return publicState(entry.state);
                    },
                },
                {
                    name: "get_logs",
                    description: "Return logs captured from the local agent process started by this canvas.",
                    handler: async (ctx) => {
                        const entry = requireOpenEntry(ctx.instanceId);
                        return {
                            running: Boolean(entry.child && !entry.child.killed),
                            agentName: entry.state.selectedAgent,
                            logs: entry.state.logs,
                        };
                    },
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    const state = createState(ctx.input || {});
                    entry = await startServer(ctx.instanceId, state);
                    servers.set(ctx.instanceId, entry);
                } else {
                    applyConfig(entry.state, ctx.input || {});
                }

                return {
                    title: entry.state.title,
                    status: entry.state.endpoint,
                    url: entry.url,
                };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    await stopAgent(entry, ctx.instanceId);
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});

function requireOpenEntry(instanceId) {
    const entry = servers.get(instanceId);
    if (!entry) {
        throw new CanvasError("canvas_not_open", "Open the Responses Chat canvas before invoking actions.");
    }
    return entry;
}

function findEntryByState(state) {
    for (const entry of servers.values()) {
        if (entry.state === state) {
            return entry;
        }
    }
    return undefined;
}

function createState(input) {
    return {
        title: normalizeTitle(input.title),
        endpoint: normalizeEndpoint(input.endpoint),
        port: DEFAULT_PORT,
        agents: [],
        selectedAgent: undefined,
        runnerStatus: "stopped",
        runnerPid: undefined,
        runnerExitCode: undefined,
        logs: [],
        draft: normalizePrompt(input.prompt),
        startupInstructions: normalizeStartupInstructions(input.startupInstructions),
        vscodeInstructions: normalizeVscodeInstructions(input.vscodeInstructions),
        quickPrompts: normalizeQuickPrompts(input.quickPrompts),
        messages: [],
        lastTrace: undefined,
        lastError: undefined,
        lastDiagnostic: undefined,
        version: 0,
    };
}

async function hydrateAgents(state) {
    state.agents = await discoverAgents();
    if (!state.selectedAgent && state.agents.length) {
        state.selectedAgent =
            state.agents.find((agent) => agent.name === "assurance-orchestrator")?.name ?? state.agents[0].name;
    }
}

function applyConfig(state, input) {
    if (typeof input.title === "string") {
        state.title = normalizeTitle(input.title);
    }
    if (typeof input.endpoint === "string") {
        state.endpoint = normalizeEndpoint(input.endpoint);
    }
    if (typeof input.prompt === "string") {
        state.draft = normalizePrompt(input.prompt);
    }
    if (typeof input.startupInstructions === "string") {
        state.startupInstructions = normalizeStartupInstructions(input.startupInstructions);
    }
    if (typeof input.vscodeInstructions === "string") {
        state.vscodeInstructions = normalizeVscodeInstructions(input.vscodeInstructions);
    }
    if (Array.isArray(input.quickPrompts)) {
        state.quickPrompts = normalizeQuickPrompts(input.quickPrompts);
    }
    touch(state);
}

async function startServer(instanceId, state) {
    await hydrateAgents(state);
    await cleanupTrackedRunner(instanceId, state);
    const server = createServer(async (req, res) => {
        try {
            await routeRequest(req, res, state);
        } catch (error) {
            state.lastError = error instanceof Error ? error.message : String(error);
            writeJson(res, 500, { error: state.lastError });
        }
    });

    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    await session.log(`Responses Chat canvas ${instanceId} listening on 127.0.0.1:${port}`, {
        level: "debug",
        ephemeral: true,
    });
    return { instanceId, server, state, url: `http://127.0.0.1:${port}/`, child: undefined };
}

async function routeRequest(req, res, state) {
    const url = new URL(req.url || "/", "http://127.0.0.1");

    if (req.method === "GET" && url.pathname === "/") {
        writeHtml(res, renderHtml());
        return;
    }

    if (req.method === "GET" && url.pathname === "/state") {
        writeJson(res, 200, publicState(state));
        return;
    }

    if (req.method === "GET" && url.pathname === "/trace") {
        writeJson(res, 200, state.lastTrace || { available: false, message: "No response trace captured yet." });
        return;
    }

    if (req.method === "GET" && url.pathname === "/agents") {
        state.agents = await discoverAgents();
        touch(state);
        writeJson(res, 200, publicState(state));
        return;
    }

    if (req.method === "POST" && url.pathname === "/config") {
        applyConfig(state, await readJson(req));
        writeJson(res, 200, publicState(state));
        return;
    }

    if (req.method === "POST" && url.pathname === "/send") {
        const body = await readJson(req);
        const message = typeof body.message === "string" ? body.message.trim() : "";
        if (!message) {
            writeJson(res, 400, { error: "Message is required." });
            return;
        }

        const endpoint = normalizeEndpoint(body.endpoint || state.endpoint);
        state.endpoint = endpoint;
        const result = await sendToResponses(endpoint, message);
        appendExchange(state, message, result);
        writeJson(res, 200, { ...result, state: publicState(state) });
        return;
    }

    if (req.method === "POST" && url.pathname === "/clear") {
        state.messages = [];
        state.lastTrace = undefined;
        state.lastError = undefined;
        touch(state);
        writeJson(res, 200, publicState(state));
        return;
    }

    if (req.method === "POST" && url.pathname === "/diagnostics") {
        const result = await runEndpointDiagnostic(state, await readJson(req));
        writeJson(res, 200, result);
        return;
    }

    if (req.method === "POST" && url.pathname === "/send-diagnostics") {
        const diagnostics = buildDiagnostics(state);
        await sendDiagnosticsToChat(diagnostics);
        writeJson(res, 200, { sent: true, diagnostics });
        return;
    }

    if (req.method === "POST" && url.pathname === "/agent/start") {
        const entry = findEntryByState(state);
        if (!entry) {
            writeJson(res, 500, { error: "Canvas entry not found." });
            return;
        }
        await startAgent(entry, await readJson(req));
        writeJson(res, 200, publicState(state));
        return;
    }

    if (req.method === "POST" && url.pathname === "/agent/stop") {
        const entry = findEntryByState(state);
        if (!entry) {
            writeJson(res, 500, { error: "Canvas entry not found." });
            return;
        }
        await stopAgent(entry);
        writeJson(res, 200, publicState(state));
        return;
    }

    writeJson(res, 404, { error: "Not found" });
}

async function runEndpointDiagnostic(state, input) {
    const endpoint = normalizeEndpoint(input.endpoint || state.endpoint);
    const message = normalizePrompt(input.message || "Reply with exactly: ok");
    state.endpoint = endpoint;

    const startedAt = new Date().toISOString();
    const started = Date.now();
    try {
        const result = await sendToResponses(endpoint, message);
        state.lastDiagnostic = {
            ok: true,
            endpoint,
            status: result.status,
            startedAt,
            durationMs: Date.now() - started,
            responsePreview: truncate(result.text || JSON.stringify(result.raw), 1200),
        };
        state.lastError = undefined;
    } catch (error) {
        state.lastDiagnostic = {
            ok: false,
            endpoint,
            startedAt,
            durationMs: Date.now() - started,
            error: error instanceof Error ? error.message : String(error),
        };
        state.lastError = state.lastDiagnostic.error;
    }
    touch(state);

    return buildDiagnostics(state);
}

async function sendDiagnosticsToChat(diagnostics) {
    await session.send({
        prompt:
            "The Responses Chat canvas sent this diagnostic snapshot. Please inspect it and suggest the next debugging step.\n\n" +
            "```json\n" +
            JSON.stringify(diagnostics, null, 2) +
            "\n```",
    });
}

async function discoverAgents() {
    const workspacePath = getWorkspacePath();
    const azureYamlPath = path.join(workspacePath, "azure.yaml");
    const text = await fs.readFile(azureYamlPath, "utf8");
    const lines = text.split(/\r?\n/);
    const agents = [];
    let inServices = false;
    let current = undefined;

    for (const line of lines) {
        if (/^services:\s*$/.test(line)) {
            inServices = true;
            continue;
        }
        if (inServices && /^\S/.test(line) && !/^services:\s*$/.test(line)) {
            break;
        }

        const serviceMatch = inServices ? line.match(/^ {4}([A-Za-z0-9_-]+):\s*$/) : undefined;
        if (serviceMatch) {
            if (current?.host === "azure.ai.agent") {
                agents.push(current);
            }
            current = { name: serviceMatch[1], project: "", startupCommand: "" };
            continue;
        }

        if (!current) {
            continue;
        }

        const projectMatch = line.match(/^ {8}project:\s*(.+?)\s*$/);
        if (projectMatch) {
            current.project = projectMatch[1].replace(/^['"]|['"]$/g, "");
            continue;
        }

        const hostMatch = line.match(/^ {8}host:\s*(.+?)\s*$/);
        if (hostMatch) {
            current.host = hostMatch[1].replace(/^['"]|['"]$/g, "");
            continue;
        }

        const startupMatch = line.match(/^ {12}startupCommand:\s*(.+?)\s*$/);
        if (startupMatch) {
            current.startupCommand = startupMatch[1].replace(/^['"]|['"]$/g, "");
        }
    }

    if (current?.host === "azure.ai.agent") {
        agents.push(current);
    }

    return agents.sort((left, right) => left.name.localeCompare(right.name));
}

async function startAgent(entry, input) {
    if (entry.child && !entry.child.killed) {
        throw new CanvasError("agent_already_running", `Agent ${entry.state.selectedAgent} is already running.`);
    }
    await cleanupTrackedRunner(entry.instanceId, entry.state);

    if (!entry.state.agents.length) {
        entry.state.agents = await discoverAgents();
    }

    const agentName = normalizeAgentName(input.agentName, entry.state);
    const port = normalizePort(input.port);
    entry.state.selectedAgent = agentName;
    entry.state.port = port;
    entry.state.endpoint = `http://localhost:${port}/responses`;
    entry.state.runnerStatus = "starting";
    entry.state.runnerExitCode = undefined;
    entry.state.logs = [];
    appendLog(entry.state, `Starting ${agentName} on port ${port} with Agent Inspector disabled.`);

    const azdCommand = "azd";
    const args = [
        "ai",
        "agent",
        "run",
        agentName,
        "--no-inspector",
        "--debug",
        "--port",
        String(port),
    ];
    let child;
    try {
        child = spawn(azdCommand, args, {
            cwd: getWorkspacePath(),
            env: sanitizedEnv(),
            detached: process.platform !== "win32",
            shell: process.platform === "win32",
            windowsHide: true,
        });
    } catch (error) {
        entry.state.runnerStatus = "error";
        entry.state.lastError = error instanceof Error ? error.message : String(error);
        appendLog(entry.state, entry.state.lastError, "error");
        throw new CanvasError("agent_spawn_failed", entry.state.lastError);
    }

    entry.child = child;
    entry.state.runnerPid = child.pid;
    await writeRunnerTracker(entry.instanceId, {
        agentName,
        pid: child.pid,
        port,
        startedAt: new Date().toISOString(),
    });
    touch(entry.state);

    child.stdout.on("data", (chunk) => {
        appendLog(entry.state, chunk.toString("utf8"), "stdout");
    });
    child.stderr.on("data", (chunk) => {
        appendLog(entry.state, chunk.toString("utf8"), "stderr");
    });
    child.on("error", (error) => {
        entry.state.runnerStatus = "error";
        entry.state.lastError = error.message;
        appendLog(entry.state, error.message, "error");
    });
    child.on("exit", (code, signal) => {
        entry.state.runnerStatus = "stopped";
        entry.state.runnerExitCode = code ?? undefined;
        entry.state.runnerPid = undefined;
        void removeRunnerTracker(entry.instanceId);
        appendLog(entry.state, `Agent process exited with code ${code ?? "null"} signal ${signal ?? "null"}.`);
    });
}

function sanitizedEnv() {
    return Object.fromEntries(
        Object.entries(process.env).filter((entry) => typeof entry[1] === "string"),
    );
}

async function stopAgent(entry, instanceId = entry.instanceId) {
    if (!entry.child || entry.child.killed) {
        await cleanupTrackedRunner(instanceId, entry.state);
        entry.state.runnerStatus = "stopped";
        touch(entry.state);
        return;
    }

    appendLog(entry.state, `Stopping agent process ${entry.child.pid}.`);
    await killProcessTree(entry.child.pid, entry.state.port);
    await removeRunnerTracker(instanceId);
    entry.state.runnerStatus = "stopping";
    touch(entry.state);
}

async function cleanupTrackedRunner(instanceId, state) {
    const tracked = await readRunnerTracker(instanceId);
    if (!tracked) {
        return;
    }

    appendLog(
        state,
        `Cleaning up previous runner pid ${tracked.pid ?? "unknown"} on port ${tracked.port ?? "unknown"}.`,
    );
    await killProcessTree(tracked.pid, tracked.port);
    await removeRunnerTracker(instanceId);
}

async function writeRunnerTracker(instanceId, value) {
    if (!value.pid) {
        return;
    }
    await fs.mkdir(RUNNER_STATE_DIR, { recursive: true });
    await fs.writeFile(runnerTrackerPath(instanceId), JSON.stringify(value, null, 2), "utf8");
}

async function readRunnerTracker(instanceId) {
    try {
        return JSON.parse(await fs.readFile(runnerTrackerPath(instanceId), "utf8"));
    } catch (error) {
        if (error?.code === "ENOENT") {
            return undefined;
        }
        throw error;
    }
}

async function removeRunnerTracker(instanceId) {
    try {
        await fs.unlink(runnerTrackerPath(instanceId));
    } catch (error) {
        if (error?.code !== "ENOENT") {
            throw error;
        }
    }
}

function runnerTrackerPath(instanceId) {
    return path.join(RUNNER_STATE_DIR, `${safeFileName(instanceId)}.json`);
}

function safeFileName(value) {
    return String(value).replace(/[^A-Za-z0-9_.-]/g, "_");
}

async function killProcessTree(pid, port) {
    if (process.platform === "win32") {
        await killWindowsProcessTree(pid, port);
        return;
    }

    if (!pid) {
        return;
    }
    try {
        process.kill(-pid, "SIGTERM");
    } catch {
        try {
            process.kill(pid, "SIGTERM");
        } catch {
            // The tracked runner already exited.
        }
    }
}

async function killWindowsProcessTree(pid, port) {
    const pidValue = Number.isInteger(pid) ? pid : 0;
    const portValue = Number.isInteger(port) ? port : 0;
    const script = `
$ErrorActionPreference = 'SilentlyContinue'
$ids = New-Object System.Collections.Generic.List[int]
if (${pidValue} -gt 0) {
  $queue = New-Object System.Collections.Generic.Queue[int]
  $queue.Enqueue(${pidValue})
  while ($queue.Count -gt 0) {
    $current = $queue.Dequeue()
    if (-not $ids.Contains($current)) { $ids.Add($current) }
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $current" | ForEach-Object {
      $queue.Enqueue([int]$_.ProcessId)
    }
  }
}
if (${portValue} -gt 0) {
  Get-NetTCPConnection -LocalPort ${portValue} -State Listen | ForEach-Object {
    if (-not $ids.Contains([int]$_.OwningProcess)) { $ids.Add([int]$_.OwningProcess) }
  }
}
$ids | Sort-Object -Descending -Unique | ForEach-Object {
  Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
}
`;
    await runProcess("powershell.exe", [
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]);
}

async function runProcess(command, args) {
    await new Promise((resolve) => {
        const child = spawn(command, args, { windowsHide: true });
        child.on("error", () => resolve());
        child.on("exit", () => resolve());
    });
}

function appendLog(state, text, stream = "info") {
    const lines = text
        .replace(/\r\n/g, "\n")
        .split("\n")
        .filter((line) => line.length);
    for (const line of lines) {
        state.logs.push({ at: new Date().toISOString(), stream, text: line });
    }
    if (state.logs.length > 500) {
        state.logs.splice(0, state.logs.length - 500);
    }
    if (state.runnerStatus === "starting" && /Starting agent on|Running on|localhost/i.test(text)) {
        state.runnerStatus = "running";
    }
    touch(state);
}

function buildDiagnostics(state) {
    const lastMessage = state.messages.at(-1);
    const lastLog = state.logs.at(-1);
    return {
        collectedAt: new Date().toISOString(),
        title: state.title,
        endpoint: state.endpoint,
        selectedAgent: state.selectedAgent,
        runnerStatus: state.runnerStatus,
        runnerPid: state.runnerPid,
        runnerExitCode: state.runnerExitCode,
        lastLog,
        messageCount: state.messages.length,
        stateVersion: state.version,
        lastMessage:
            lastMessage && {
                role: lastMessage.role,
                at: lastMessage.at,
                textPreview: truncate(lastMessage.text || "", 1200),
            },
        lastError: state.lastError,
        lastDiagnostic: state.lastDiagnostic,
    };
}

async function sendToResponses(endpoint, message) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), RESPONSES_TIMEOUT_MS);

    try {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                input: message,
                stream: false,
                store: false,
            }),
            signal: controller.signal,
        });

        const rawText = await response.text();
        const json = parseJson(rawText);
        if (!response.ok) {
            const detail = json ? JSON.stringify(json, null, 2) : rawText;
            throw new Error(`Responses API returned ${response.status}: ${detail}`);
        }

        return {
            endpoint,
            status: response.status,
            text: extractResponseText(json) || rawText,
            raw: json ?? rawText,
        };
    } finally {
        clearTimeout(timeout);
    }
}

function appendExchange(state, userText, result) {
    state.lastError = undefined;
    state.lastTrace = buildTrace(result, userText);
    state.messages.push({ role: "user", text: userText, at: new Date().toISOString() });
    state.messages.push({
        role: "assistant",
        text: result.text || JSON.stringify(result.raw, null, 2),
        raw: result.raw,
        at: new Date().toISOString(),
    });
    touch(state);
}

function buildTrace(result, userText) {
    const raw = result?.raw;
    const output = raw && typeof raw === "object" && Array.isArray(raw.output) ? raw.output : [];
    const timeline = [];
    const calls = new Map();
    for (const item of output) {
        if (item?.type === "function_call") {
            const entry = {
                type: "function_call",
                callId: item.call_id,
                name: item.name,
                arguments: parseJson(item.arguments) ?? item.arguments,
                status: item.status,
            };
            calls.set(item.call_id, entry);
            timeline.push(entry);
            continue;
        }

        if (item?.type === "function_call_output") {
            const parsedOutput = parseJson(item.output) ?? item.output;
            const entry = {
                type: "function_call_output",
                callId: item.call_id,
                name: calls.get(item.call_id)?.name,
                status: item.status,
                output: parsedOutput,
            };
            timeline.push(entry);
            continue;
        }

        if (item?.type === "message") {
            timeline.push({
                type: "message",
                role: item.role,
                status: item.status,
                text: extractResponseText({ output: [item] }),
            });
            continue;
        }

        timeline.push({
            type: item?.type || "unknown",
            status: item?.status,
            raw: item,
        });
    }

    return {
        available: true,
        capturedAt: new Date().toISOString(),
        endpoint: result?.endpoint,
        httpStatus: result?.status,
        prompt: userText,
        response: raw && typeof raw === "object"
            ? {
                id: raw.id,
                response_id: raw.response_id,
                status: raw.status,
                model: raw.model,
                agent_session_id: raw.agent_session_id,
                output_count: output.length,
            }
            : undefined,
        timeline,
        raw,
    };
}

function touch(state) {
    state.version += 1;
}

function extractResponseText(value) {
    if (!value || typeof value !== "object") {
        return "";
    }

    if (typeof value.output_text === "string") {
        return value.output_text;
    }

    if (Array.isArray(value.output)) {
        const chunks = [];
        for (const item of value.output) {
            if (typeof item?.content === "string") {
                chunks.push(item.content);
            }
            if (Array.isArray(item?.content)) {
                for (const part of item.content) {
                    if (typeof part?.text === "string") {
                        chunks.push(part.text);
                    } else if (typeof part?.content === "string") {
                        chunks.push(part.content);
                    }
                }
            }
        }
        return chunks.join("\n").trim();
    }

    if (typeof value.text === "string") {
        return value.text;
    }

    if (typeof value.message === "string") {
        return value.message;
    }

    return "";
}

function normalizeEndpoint(endpoint) {
    if (typeof endpoint !== "string" || !endpoint.trim()) {
        return DEFAULT_ENDPOINT;
    }

    return endpoint.trim();
}

function normalizeAgentName(agentName, state) {
    const cleanName = typeof agentName === "string" ? agentName.trim() : "";
    const fallback = state.selectedAgent || state.agents[0]?.name;
    const selected = cleanName || fallback;
    if (!selected) {
        throw new CanvasError("no_agent_selected", "No Azure AI agent services were discovered in azure.yaml.");
    }

    if (!state.agents.some((agent) => agent.name === selected)) {
        throw new CanvasError("unknown_agent", `Agent ${selected} was not found in azure.yaml.`);
    }

    return selected;
}

function normalizePort(port) {
    const value = Number(port || DEFAULT_PORT);
    if (!Number.isInteger(value) || value < 1 || value > 65535) {
        throw new CanvasError("invalid_port", "Port must be an integer from 1 to 65535.");
    }
    return value;
}

function getWorkspacePath() {
    let current = EXTENSION_DIR;
    for (let depth = 0; depth < 8; depth += 1) {
        const candidate = path.join(current, "azure.yaml");
        if (existsSync(candidate)) {
            return current;
        }

        const parent = path.dirname(current);
        if (parent === current) {
            break;
        }
        current = parent;
    }

    throw new CanvasError("workspace_unavailable", "Could not find azure.yaml from the extension folder.");
}

function normalizeTitle(title) {
    if (typeof title !== "string" || !title.trim()) {
        return DEFAULT_TITLE;
    }

    return title.trim();
}

function normalizePrompt(prompt) {
    if (typeof prompt !== "string" || !prompt.trim()) {
        return DEFAULT_PROMPT;
    }

    return prompt.trim();
}

function normalizeStartupInstructions(startupInstructions) {
    if (typeof startupInstructions !== "string" || !startupInstructions.trim()) {
        return DEFAULT_STARTUP_INSTRUCTIONS;
    }

    return startupInstructions.trim();
}

function normalizeVscodeInstructions(vscodeInstructions) {
    if (typeof vscodeInstructions !== "string" || !vscodeInstructions.trim()) {
        return DEFAULT_VSCODE_INSTRUCTIONS;
    }

    return vscodeInstructions.trim();
}

function normalizeQuickPrompts(quickPrompts) {
    if (!Array.isArray(quickPrompts)) {
        return DEFAULT_QUICK_PROMPTS;
    }

    const normalized = quickPrompts
        .filter((item) => typeof item?.label === "string" && typeof item?.prompt === "string")
        .map((item) => ({ label: item.label.trim(), prompt: item.prompt.trim() }))
        .filter((item) => item.label && item.prompt);
    return normalized.length ? normalized : DEFAULT_QUICK_PROMPTS;
}

function publicState(state) {
    return {
        title: state.title,
        endpoint: state.endpoint,
        port: state.port,
        agents: state.agents,
        selectedAgent: state.selectedAgent,
        runnerStatus: state.runnerStatus,
        runnerPid: state.runnerPid,
        runnerExitCode: state.runnerExitCode,
        logs: state.logs,
        draft: state.draft,
        startupInstructions: state.startupInstructions,
        vscodeInstructions: state.vscodeInstructions,
        quickPrompts: state.quickPrompts,
        messages: state.messages,
        lastTrace: state.lastTrace,
        lastError: state.lastError,
        lastDiagnostic: state.lastDiagnostic,
        version: state.version,
    };
}

function truncate(text, maxLength) {
    if (text.length <= maxLength) {
        return text;
    }

    return `${text.slice(0, maxLength)}...`;
}

async function readJson(req) {
    const chunks = [];
    for await (const chunk of req) {
        chunks.push(chunk);
    }

    const text = Buffer.concat(chunks).toString("utf8");
    if (!text) {
        return {};
    }

    return JSON.parse(text);
}

function parseJson(text) {
    try {
        return JSON.parse(text);
    } catch {
        return undefined;
    }
}

function writeHtml(res, html) {
    res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
    });
    res.end(html);
}

function writeJson(res, status, value) {
    res.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    });
    res.end(JSON.stringify(value));
}

function renderHtml() {
    return String.raw`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Responses Chat</title>
  <style>
    :root {
      color-scheme: light;
      scrollbar-color: var(--border-color-default, #d0d7de) var(--background-color-default, #ffffff);
    }

    * {
      box-sizing: border-box;
    }

    *::-webkit-scrollbar {
      height: 12px;
      width: 12px;
    }

    *::-webkit-scrollbar-track {
      background: var(--background-color-default, #ffffff);
    }

    *::-webkit-scrollbar-thumb {
      background-color: var(--border-color-default, #d0d7de);
      border: 3px solid var(--background-color-default, #ffffff);
      border-radius: 999px;
    }

    *::-webkit-scrollbar-thumb:hover {
      background-color: var(--text-color-muted, #57606a);
    }

    body {
      margin: 0;
      background: var(--background-color-default, #ffffff);
      color: var(--text-color-default, #1f2328);
      font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
      font-size: var(--text-body-medium, 14px);
      line-height: var(--leading-body-medium, 20px);
    }

    main {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100vh;
      min-height: 0;
    }

    header,
    form {
      border-bottom: 1px solid var(--border-color-default, #d0d7de);
      padding: 8px 10px;
    }

    form {
      border-bottom: 0;
      border-top: 1px solid var(--border-color-default, #d0d7de);
    }

    h1 {
      font-size: 16px;
      line-height: 22px;
      margin: 0 0 8px;
    }

    .header-row {
      align-items: center;
      display: flex;
      gap: 8px;
      justify-content: space-between;
      margin-bottom: 6px;
    }

    .header-row h1 {
      margin: 0;
    }

    label {
      display: block;
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      margin-bottom: 4px;
    }

    input,
    textarea,
    button {
      font: inherit;
    }

    input,
    textarea {
      width: 100%;
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 6px;
      background: var(--background-color-default, #ffffff);
      color: var(--text-color-default, #1f2328);
      padding: 8px;
    }

    textarea {
      min-height: 92px;
      resize: vertical;
    }

    button {
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 6px;
      background: var(--button-default-bgColor-rest, var(--background-color-default, #ffffff));
      color: var(--text-color-default, #1f2328);
      cursor: pointer;
      padding: 8px 10px;
    }

    button.primary {
      background: var(--button-primary-bgColor-rest, #1f883d);
      border-color: var(--button-primary-borderColor-rest, #1f883d);
      color: var(--button-primary-fgColor-rest, #ffffff);
    }

    button:disabled {
      cursor: wait;
      opacity: 0.65;
    }

    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }

    .row > button {
      flex: 0 0 auto;
    }

    .compact-actions {
      align-items: end;
      flex-wrap: nowrap;
      margin-top: 0;
    }

    .compact-actions button {
      padding: 6px 8px;
    }

    #messages,
    #debug,
    #trace {
      min-height: 0;
      overflow: auto;
      padding: 12px;
    }

    .content {
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 0;
      overflow: hidden;
    }

    .content-tabs {
      border-bottom: 1px solid var(--border-color-default, #d0d7de);
      display: flex;
      gap: 6px;
      padding: 8px 10px 0;
    }

    .content-tab {
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
      padding: 6px 10px;
    }

    .content-tab.active {
      border-color: var(--color-focus-outline, #0969da);
      box-shadow: inset 0 -2px 0 var(--color-focus-outline, #0969da);
      font-weight: var(--font-weight-semibold, 600);
    }

    .content-panel {
      min-height: 0;
      overflow: auto;
    }

    .content-panel.hidden {
      display: none;
    }

    .panel-card,
    .trace-card {
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 8px;
      margin-bottom: 10px;
      padding: 10px;
    }

    .trace-meta {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      margin-bottom: 8px;
    }

    .panel-card h3,
    .trace-card h3 {
      font-size: 14px;
      line-height: 20px;
      margin: 0 0 6px;
    }

    .debug-grid {
      display: grid;
      gap: 10px;
    }

    .trace-card pre {
      max-height: 360px;
    }

    .message {
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 8px;
      margin-bottom: 10px;
      padding: 10px;
      white-space: pre-wrap;
    }

    .message .markdown {
      white-space: normal;
    }

    .message .markdown > :first-child {
      margin-top: 0;
    }

    .message .markdown > :last-child {
      margin-bottom: 0;
    }

    .message .markdown h1,
    .message .markdown h2,
    .message .markdown h3 {
      font-size: 14px;
      line-height: 20px;
      margin: 12px 0 6px;
    }

    .message .markdown p,
    .message .markdown ul,
    .message .markdown ol,
    .message .markdown table {
      margin: 0 0 8px;
    }

    .message .markdown ul,
    .message .markdown ol {
      padding-left: 22px;
    }

    .message .markdown code {
      background: var(--background-color-muted, rgba(175, 184, 193, 0.16));
      border-radius: 4px;
      font-family: var(--font-mono, Consolas, monospace);
      font-size: var(--text-code-inline, 12px);
      padding: 1px 4px;
    }

    .message .markdown pre code {
      background: transparent;
      border-radius: 0;
      display: block;
      overflow: auto;
      padding: 0;
      white-space: pre;
    }

    .message .markdown table {
      border-collapse: collapse;
      display: block;
      overflow: auto;
      width: 100%;
    }

    .message .markdown th,
    .message .markdown td {
      border: 1px solid var(--border-color-default, #d0d7de);
      padding: 4px 6px;
      text-align: left;
      vertical-align: top;
    }

    .message .markdown th {
      background: var(--background-color-muted, rgba(175, 184, 193, 0.12));
      font-weight: var(--font-weight-semibold, 600);
    }

    .message .markdown a {
      color: var(--color-focus-outline, #0969da);
    }

    .message.user {
      background: var(--control-transparent-bgColor-hover, rgba(175, 184, 193, 0.12));
    }

    .message.assistant {
      background: var(--background-color-muted, rgba(175, 184, 193, 0.08));
    }

    .role {
      color: var(--text-color-muted, #57606a);
      display: block;
      font-size: 12px;
      font-weight: var(--font-weight-semibold, 600);
      margin-bottom: 4px;
      text-transform: capitalize;
    }

    .status {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      margin-top: 8px;
      min-height: 18px;
    }

    .runner {
      margin-top: 6px;
    }

    .runner-grid {
      align-items: end;
      display: grid;
      gap: 6px;
      grid-template-columns: minmax(130px, 0.8fr) 70px minmax(160px, 1.4fr) auto;
    }

    select {
      width: 100%;
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 6px;
      background: var(--background-color-default, #ffffff);
      color: var(--text-color-default, #1f2328);
      font: inherit;
      padding: 6px 8px;
    }

    .logs {
      background: var(--background-color-muted, rgba(175, 184, 193, 0.08));
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 8px;
      font-family: var(--font-mono, Consolas, monospace);
      font-size: var(--text-code-inline, 12px);
      max-height: 120px;
      overflow: auto;
      padding: 8px;
      white-space: pre-wrap;
    }

    details.logs-panel {
      margin-top: 6px;
    }

    details.logs-panel summary {
      color: var(--text-color-muted, #57606a);
      cursor: pointer;
      font-size: 12px;
    }

    .error {
      color: var(--true-color-red, #cf222e);
    }

    .modal-backdrop {
      align-items: center;
      background: rgba(31, 35, 40, 0.48);
      display: none;
      inset: 0;
      justify-content: center;
      padding: 24px;
      position: fixed;
      z-index: 10;
    }

    .modal-backdrop.open {
      display: flex;
    }

    .modal {
      background: var(--background-color-default, #ffffff);
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 10px;
      box-shadow: 0 12px 32px rgba(31, 35, 40, 0.24);
      color: var(--text-color-default, #1f2328);
      max-height: min(720px, calc(100vh - 48px));
      max-width: 720px;
      overflow: auto;
      padding: 16px;
      width: 100%;
    }

    .modal-header {
      align-items: center;
      display: flex;
      gap: 8px;
      justify-content: space-between;
      margin-bottom: 12px;
    }

    .modal-header h2 {
      font-size: 16px;
      line-height: 22px;
      margin: 0;
    }

    .tabs {
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }

    .tab {
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
    }

    .tab.active {
      border-color: var(--color-focus-outline, #0969da);
      box-shadow: inset 0 -2px 0 var(--color-focus-outline, #0969da);
      font-weight: var(--font-weight-semibold, 600);
    }

    pre {
      background: var(--background-color-muted, rgba(175, 184, 193, 0.08));
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 8px;
      margin: 0;
      overflow: auto;
      padding: 12px;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="header-row">
        <h1 id="title">Responses Chat</h1>
        <button id="startup" type="button">Startup instructions</button>
      </div>
      <section class="runner" aria-label="Local agent runner">
        <div class="runner-grid">
          <div>
            <label for="agent">Agent</label>
            <select id="agent"></select>
          </div>
          <div>
            <label for="port">Port</label>
            <input id="port" inputmode="numeric" />
          </div>
          <div>
            <label for="endpoint">Endpoint</label>
            <input id="endpoint" spellcheck="false" />
          </div>
          <div class="row compact-actions">
            <button id="start-agent" type="button">Start</button>
            <button id="stop-agent" type="button">Stop</button>
            <button id="refresh-agents" type="button">Refresh</button>
          </div>
        </div>
        <div id="runner-status" class="status">Runner stopped</div>
        <div id="status" class="status">Loading...</div>
      </section>
    </header>

    <section class="content">
      <div class="content-tabs" role="tablist" aria-label="Response views">
        <button class="content-tab active" id="chat-tab" type="button" role="tab" aria-selected="true">Chat</button>
        <button class="content-tab" id="debug-tab" type="button" role="tab" aria-selected="false">Debug</button>
        <button class="content-tab" id="trace-tab" type="button" role="tab" aria-selected="false">Trace</button>
      </div>
      <section id="messages" class="content-panel" aria-live="polite"></section>
      <section id="debug" class="content-panel hidden" aria-live="polite">
        <div class="debug-grid">
          <section class="panel-card">
            <h3>Diagnostics</h3>
            <pre id="diagnostics"></pre>
          </section>
          <section class="panel-card">
            <h3>Agent logs</h3>
            <div id="logs" class="logs" aria-live="polite"></div>
          </section>
        </div>
      </section>
      <section id="trace" class="content-panel hidden" aria-live="polite"></section>
    </section>

    <form id="chat-form">
      <label for="message">Prompt</label>
      <textarea id="message"></textarea>
      <div class="row">
        <button class="primary" id="send" type="submit">Send</button>
        <span id="quick-prompts"></span>
        <button id="check-endpoint" type="button">Check endpoint</button>
        <button id="send-diagnostics" type="button">Send diagnostics to Copilot</button>
        <button id="clear" type="button">Clear</button>
      </div>
    </form>
  </main>

  <div id="startup-modal" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="startup-title">
    <section class="modal">
      <div class="modal-header">
        <h2 id="startup-title">Start and debug a local agent</h2>
        <button id="close-startup" type="button">Close</button>
      </div>
      <div class="tabs" role="tablist" aria-label="Startup instruction views">
        <button class="tab active" id="terminal-tab" type="button" role="tab" aria-selected="true">Terminal</button>
        <button class="tab" id="vscode-tab" type="button" role="tab" aria-selected="false">VS Code</button>
      </div>
      <pre id="startup-instructions"></pre>
      <div class="row">
        <button id="copy-startup" type="button">Copy instructions</button>
      </div>
    </section>
  </div>

  <script>
    const title = document.querySelector("#title");
    const endpoint = document.querySelector("#endpoint");
    const agent = document.querySelector("#agent");
    const port = document.querySelector("#port");
    const startAgent = document.querySelector("#start-agent");
    const stopAgent = document.querySelector("#stop-agent");
    const refreshAgents = document.querySelector("#refresh-agents");
    const runnerStatus = document.querySelector("#runner-status");
    const logs = document.querySelector("#logs");
    const message = document.querySelector("#message");
    const messages = document.querySelector("#messages");
    const debug = document.querySelector("#debug");
    const diagnostics = document.querySelector("#diagnostics");
    const trace = document.querySelector("#trace");
    const chatTab = document.querySelector("#chat-tab");
    const debugTab = document.querySelector("#debug-tab");
    const traceTab = document.querySelector("#trace-tab");
    const status = document.querySelector("#status");
    const send = document.querySelector("#send");
    const quickPrompts = document.querySelector("#quick-prompts");
    const checkEndpoint = document.querySelector("#check-endpoint");
    const sendDiagnostics = document.querySelector("#send-diagnostics");
    const clear = document.querySelector("#clear");
    const form = document.querySelector("#chat-form");
    const startup = document.querySelector("#startup");
    const startupModal = document.querySelector("#startup-modal");
    const startupInstructions = document.querySelector("#startup-instructions");
    const closeStartup = document.querySelector("#close-startup");
    const copyStartup = document.querySelector("#copy-startup");
    const terminalTab = document.querySelector("#terminal-tab");
    const vscodeTab = document.querySelector("#vscode-tab");
    let startupText = "";
    let vscodeText = "";
    let activeInstructionTab = "terminal";
    let activeContentTab = "chat";
    let stateVersion = -1;
    let busy = false;

    async function loadState(force = false) {
      const state = await fetchJson("/state");
      if (!force && state.version === stateVersion) {
        return;
      }
      stateVersion = state.version;
      title.textContent = state.title;
      document.title = state.title;
      endpoint.value = state.endpoint;
      port.value = String(state.port || 8088);
      renderAgents(state.agents || [], state.selectedAgent);
      renderRunner(state);
      renderDiagnostics(state);
      message.value = state.draft;
      startupText = state.startupInstructions || "";
      vscodeText = state.vscodeInstructions || "";
      renderInstructionTab();
      renderQuickPrompts(state.quickPrompts || []);
      renderMessages(state.messages || []);
      renderTrace(state.lastTrace);
      renderContentTab();
      setStatus(formatStatus(state), Boolean(state.lastError));
    }

    async function refreshFromServer() {
      if (busy) {
        return;
      }

      try {
        await loadState();
      } catch {
        // The explicit send/check paths surface request errors to the user.
      }
    }

    async function sendMessage(text) {
      const prompt = text.trim();
      if (!prompt) {
        setStatus("Enter a prompt first.", true);
        return;
      }

      setBusy(true);
      setStatus("Sending...");
      try {
        const result = await fetchJson("/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: endpoint.value, message: prompt })
        });
        renderMessages(result.state.messages || []);
        renderTrace(result.state.lastTrace);
        activeContentTab = "trace";
        renderContentTab();
        stateVersion = result.state.version;
        setStatus("Last response: HTTP " + result.status);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    }

    function renderQuickPrompts(items) {
      quickPrompts.innerHTML = "";
      for (const item of items) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.label;
        button.addEventListener("click", () => {
          message.value = item.prompt;
          sendMessage(item.prompt);
        });
        quickPrompts.append(button);
        quickPrompts.append(" ");
      }
    }

    function renderAgents(items, selectedAgent) {
      const currentValue = agent.value || selectedAgent;
      agent.innerHTML = "";
      for (const item of items) {
        const option = document.createElement("option");
        option.value = item.name;
        option.textContent = item.name;
        if (item.project) {
          option.title = item.project;
        }
        agent.append(option);
      }
      agent.value = selectedAgent || currentValue || items[0]?.name || "";
    }

    function renderRunner(state) {
      const pid = state.runnerPid ? " pid " + state.runnerPid : "";
      const exit = state.runnerExitCode !== undefined ? " exit " + state.runnerExitCode : "";
      runnerStatus.textContent = "Runner " + (state.runnerStatus || "stopped") + pid + exit;
      logs.textContent = (state.logs || [])
        .slice(-80)
        .map((entry) => "[" + entry.stream + "] " + entry.text)
        .join("\\n");
      logs.scrollTop = logs.scrollHeight;
    }

    function renderDiagnostics(state) {
      diagnostics.textContent = JSON.stringify({
        endpoint: state.endpoint,
        selectedAgent: state.selectedAgent,
        runnerStatus: state.runnerStatus,
        runnerPid: state.runnerPid,
        runnerExitCode: state.runnerExitCode,
        lastDiagnostic: state.lastDiagnostic,
        lastError: state.lastError,
        messageCount: (state.messages || []).length,
        stateVersion: state.version,
      }, null, 2);
    }

    function renderMessages(items) {
      messages.innerHTML = "";
      for (const item of items) {
        const article = document.createElement("article");
        article.className = "message " + item.role;

        const role = document.createElement("span");
        role.className = "role";
        role.textContent = item.role;

        const text = document.createElement("div");
        if (item.role === "assistant") {
          text.className = "markdown";
          text.innerHTML = renderMarkdown(item.text || "");
        } else {
          text.textContent = item.text || "";
        }

        article.append(role, text);
        messages.append(article);
      }
      messages.scrollTop = messages.scrollHeight;
    }

    function renderTrace(value) {
      trace.innerHTML = "";
      if (!value || value.available === false) {
        const empty = document.createElement("div");
        empty.className = "trace-card";
        empty.textContent = "No trace captured yet. Send a prompt to capture the raw Responses output and tool timeline.";
        trace.append(empty);
        return;
      }

      const summary = document.createElement("section");
      summary.className = "trace-card";
      summary.append(traceHeading("Summary"));
      const meta = document.createElement("div");
      meta.className = "trace-meta";
      meta.textContent = [
        value.capturedAt,
        value.endpoint,
        value.response?.id || value.response?.response_id,
        "status=" + (value.response?.status || value.httpStatus || "unknown"),
        "outputs=" + (value.response?.output_count ?? 0),
      ].filter(Boolean).join(" · ");
      summary.append(meta);
      summary.append(tracePre({
        prompt: value.prompt,
        response: value.response,
      }));
      trace.append(summary);

      const timeline = document.createElement("section");
      timeline.className = "trace-card";
      timeline.append(traceHeading("Timeline"));
      timeline.append(tracePre(value.timeline || []));
      trace.append(timeline);

      const raw = document.createElement("section");
      raw.className = "trace-card";
      raw.append(traceHeading("Raw response"));
      raw.append(tracePre(value.raw));
      trace.append(raw);
    }

    function traceHeading(text) {
      const heading = document.createElement("h3");
      heading.textContent = text;
      return heading;
    }

    function tracePre(value) {
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(value, null, 2);
      return pre;
    }

    function renderContentTab() {
      const showingChat = activeContentTab === "chat";
      const showingDebug = activeContentTab === "debug";
      const showingTrace = activeContentTab === "trace";
      chatTab.classList.toggle("active", showingChat);
      chatTab.setAttribute("aria-selected", String(showingChat));
      debugTab.classList.toggle("active", showingDebug);
      debugTab.setAttribute("aria-selected", String(showingDebug));
      traceTab.classList.toggle("active", showingTrace);
      traceTab.setAttribute("aria-selected", String(showingTrace));
      messages.classList.toggle("hidden", !showingChat);
      debug.classList.toggle("hidden", !showingDebug);
      trace.classList.toggle("hidden", !showingTrace);
    }

    function renderMarkdown(source) {
      const blocks = [];
      const lines = String(source).replace(/\r\n/g, "\n").split("\n");
      for (let index = 0; index < lines.length;) {
        if (/^\s*\x60\x60\x60/.test(lines[index])) {
          const code = [];
          index += 1;
          while (index < lines.length && !/^\s*\x60\x60\x60/.test(lines[index])) {
            code.push(lines[index]);
            index += 1;
          }
          index += index < lines.length ? 1 : 0;
          blocks.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
          continue;
        }

        if (/^\s*\|.*\|\s*$/.test(lines[index]) && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1] || "")) {
          const tableLines = [lines[index]];
          index += 2;
          while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
            tableLines.push(lines[index]);
            index += 1;
          }
          blocks.push(renderMarkdownTable(tableLines));
          continue;
        }

        if (/^\s*#{1,3}\s+/.test(lines[index])) {
          const match = lines[index].match(/^\s*(#{1,3})\s+(.+)$/);
          const level = match[1].length;
          blocks.push("<h" + level + ">" + renderInlineMarkdown(match[2]) + "</h" + level + ">");
          index += 1;
          continue;
        }

        if (/^\s*[-*]\s+/.test(lines[index])) {
          const items = [];
          while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
            items.push("<li>" + renderInlineMarkdown(lines[index].replace(/^\s*[-*]\s+/, "")) + "</li>");
            index += 1;
          }
          blocks.push("<ul>" + items.join("") + "</ul>");
          continue;
        }

        if (/^\s*\d+\.\s+/.test(lines[index])) {
          const items = [];
          while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
            items.push("<li>" + renderInlineMarkdown(lines[index].replace(/^\s*\d+\.\s+/, "")) + "</li>");
            index += 1;
          }
          blocks.push("<ol>" + items.join("") + "</ol>");
          continue;
        }

        if (!lines[index].trim()) {
          index += 1;
          continue;
        }

        const paragraph = [];
        while (
          index < lines.length &&
          lines[index].trim() &&
          !/^\s*\x60\x60\x60/.test(lines[index]) &&
          !/^\s*#{1,3}\s+/.test(lines[index]) &&
          !/^\s*[-*]\s+/.test(lines[index]) &&
          !/^\s*\d+\.\s+/.test(lines[index]) &&
          !(/^\s*\|.*\|\s*$/.test(lines[index]) && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1] || ""))
        ) {
          paragraph.push(lines[index]);
          index += 1;
        }
        blocks.push("<p>" + renderInlineMarkdown(paragraph.join("\n")) + "</p>");
      }
      return blocks.join("");
    }

    function renderMarkdownTable(lines) {
      const rows = lines.map(parseMarkdownTableRow).filter((row) => row.length);
      if (!rows.length) {
        return "";
      }
      const header = rows[0];
      const body = rows.slice(1);
      return "<table><thead><tr>" +
        header.map((cell) => "<th>" + renderInlineMarkdown(cell) + "</th>").join("") +
        "</tr></thead><tbody>" +
        body.map((row) => "<tr>" + row.map((cell) => "<td>" + renderInlineMarkdown(cell) + "</td>").join("") + "</tr>").join("") +
        "</tbody></table>";
    }

    function parseMarkdownTableRow(line) {
      return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
    }

    function renderInlineMarkdown(source) {
      return escapeHtml(source)
        .replace(/\x60([^\x60]+)\x60/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*]+)\*/g, "<em>$1</em>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
        .replace(/\n/g, "<br />");
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      const json = await response.json();
      if (!response.ok) {
        throw new Error(json.error || "Request failed");
      }
      return json;
    }

    function setBusy(isBusy) {
      busy = isBusy;
      send.disabled = busy;
      checkEndpoint.disabled = busy;
      sendDiagnostics.disabled = busy;
      startAgent.disabled = busy;
      stopAgent.disabled = busy;
      refreshAgents.disabled = busy;
      clear.disabled = busy;
      for (const button of quickPrompts.querySelectorAll("button")) {
        button.disabled = busy;
      }
    }

    function setStatus(text, isError = false) {
      status.textContent = text;
      status.classList.toggle("error", isError);
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      sendMessage(message.value);
    });

    clear.addEventListener("click", async () => {
      setBusy(true);
      try {
        await fetchJson("/clear", { method: "POST" });
        stateVersion = -1;
        renderMessages([]);
        renderTrace(undefined);
        activeContentTab = "chat";
        renderContentTab();
        setStatus("Cleared");
      } finally {
        setBusy(false);
      }
    });

    startAgent.addEventListener("click", async () => {
      setBusy(true);
      setStatus("Starting agent...");
      try {
        const state = await fetchJson("/agent/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agentName: agent.value, port: Number(port.value || 8088) })
        });
        stateVersion = state.version;
        endpoint.value = state.endpoint;
        renderRunner(state);
        setStatus("Agent start requested");
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    });

    stopAgent.addEventListener("click", async () => {
      setBusy(true);
      setStatus("Stopping agent...");
      try {
        const state = await fetchJson("/agent/stop", { method: "POST" });
        stateVersion = state.version;
        renderRunner(state);
        setStatus("Agent stop requested");
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    });

    refreshAgents.addEventListener("click", async () => {
      setBusy(true);
      try {
        const state = await fetchJson("/agents");
        stateVersion = state.version;
        renderAgents(state.agents || [], state.selectedAgent);
        renderRunner(state);
        setStatus("Agent list refreshed");
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    });

    chatTab.addEventListener("click", () => {
      activeContentTab = "chat";
      renderContentTab();
    });

    debugTab.addEventListener("click", () => {
      activeContentTab = "debug";
      renderContentTab();
    });

    traceTab.addEventListener("click", () => {
      activeContentTab = "trace";
      renderContentTab();
    });

    checkEndpoint.addEventListener("click", async () => {
      setBusy(true);
      setStatus("Checking endpoint...");
      try {
        const diagnostics = await fetchJson("/diagnostics", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: endpoint.value })
        });
        const diagnostic = diagnostics.lastDiagnostic;
        activeContentTab = "debug";
        renderContentTab();
        setStatus(diagnostic.ok ? "Endpoint check passed: HTTP " + diagnostic.status : diagnostic.error, !diagnostic.ok);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    });

    sendDiagnostics.addEventListener("click", async () => {
      setBusy(true);
      try {
        await fetchJson("/send-diagnostics", { method: "POST" });
        setStatus("Diagnostics sent to Copilot chat");
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    });

    startup.addEventListener("click", () => {
      startupModal.classList.add("open");
    });

    closeStartup.addEventListener("click", () => {
      startupModal.classList.remove("open");
    });

    startupModal.addEventListener("click", (event) => {
      if (event.target === startupModal) {
        startupModal.classList.remove("open");
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        startupModal.classList.remove("open");
      }
    });

    copyStartup.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(activeInstructionTab === "vscode" ? vscodeText : startupText);
        setStatus("Startup instructions copied");
      } catch (error) {
        setStatus(error.message, true);
      }
    });

    terminalTab.addEventListener("click", () => {
      activeInstructionTab = "terminal";
      renderInstructionTab();
    });

    vscodeTab.addEventListener("click", () => {
      activeInstructionTab = "vscode";
      renderInstructionTab();
    });

    function renderInstructionTab() {
      const isVscode = activeInstructionTab === "vscode";
      terminalTab.classList.toggle("active", !isVscode);
      terminalTab.setAttribute("aria-selected", String(!isVscode));
      vscodeTab.classList.toggle("active", isVscode);
      vscodeTab.setAttribute("aria-selected", String(isVscode));
      startupInstructions.textContent = isVscode ? vscodeText : startupText;
      copyStartup.textContent = isVscode ? "Copy VS Code instructions" : "Copy terminal instructions";
    }

    function formatStatus(state) {
      if (state.lastError) {
        return state.lastError;
      }

      if (state.lastDiagnostic) {
        return state.lastDiagnostic.ok
          ? "Last endpoint check passed: HTTP " + state.lastDiagnostic.status
          : "Last endpoint check failed";
      }

      return "Ready";
    }

    endpoint.addEventListener("change", async () => {
      try {
        await fetchJson("/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: endpoint.value, draft: message.value })
        });
        setStatus("Endpoint saved");
      } catch (error) {
        setStatus(error.message, true);
      }
    });

    message.addEventListener("change", async () => {
      try {
        await fetchJson("/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: endpoint.value, prompt: message.value })
        });
      } catch (error) {
        setStatus(error.message, true);
      }
    });

    loadState().catch((error) => setStatus(error.message, true));
    setInterval(refreshFromServer, 1000);
  </script>
</body>
</html>`;
}
