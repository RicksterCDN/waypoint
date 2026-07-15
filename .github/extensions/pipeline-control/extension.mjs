// Extension: pipeline-control
// Live mission-control for the local pharma invoice-assurance pipeline. Shows
// Waypoint + Assurance Orchestrator status, lets you trigger an assurance run, and renders the
// resulting workflow output plus the Waypoint cases/audit the run produced.
//
// The canvas OBSERVES and TRIGGERS over HTTP, and can also SPIN UP the full local
// stack on demand: the Forge stack toggle (and the `start_stack` action)
// launch the local hosted IQ-tool agents and Assurance Orchestrator as detached processes that
// survive session teardown. Bare `azd`/`npm` are resolved to absolute paths and
// run with an augmented PATH to dodge the GUI-app PATH problem. The same toggle
// can tear those Forge-local processes back down. Waypoint remains
// Aspire-managed in the Waypoint repo and has its own start/stop toggle.

import { createServer } from "node:http";
import net from "node:net";
import { spawn, execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { joinSession, createCanvas, CanvasError } from "@github/copilot-sdk/extension";

// Local Aspire commonly exposes HTTPS endpoints with dev certificates. The
// Assurance Orchestrator process still gets WAYPOINT_API_VERIFY_SSL=false; this lets the canvas
// probe the same local API boundary.
process.env.NODE_TLS_REJECT_UNAUTHORIZED ||= "0";

// Forge repo root (where `azd ai agent run` must execute). The extension always
// lives at <forgeRoot>/.github/extensions/pipeline-control/extension.mjs, so we
// derive the repo root from this file's own location — session.workspacePath is
// the session-state dir, not the worktree, so it can't be used here.
const EXT_DIR = path.dirname(fileURLToPath(import.meta.url));
let WORKSPACE_PATH = path.resolve(EXT_DIR, "..", "..", "..");

// ---------------------------------------------------------------------------
// Topology — the nodes of the local pipeline and the ports they listen on.
// ---------------------------------------------------------------------------
const BOOTSTRAP_REPO_ENV = readRepoEnvValues(WORKSPACE_PATH);
const IQ_MODE = (
    process.env.PIPELINE_IQ_MODE ||
    process.env.LOCAL_IQ_MODE ||
    BOOTSTRAP_REPO_ENV.PIPELINE_IQ_MODE ||
    BOOTSTRAP_REPO_ENV.LOCAL_IQ_MODE ||
    "stub"
).toLowerCase() === "hosted"
    ? "hosted"
    : "stub";
const PROMPT_VALIDATOR_NODE = "prompt-validators";
const PROMPT_AGENT_NODES = [
    { id: "workiq", agentName: "collaboration-evidence-expert", label: "collaboration-evidence-expert", hostedPort: 8091, stubEndpoint: "/workiq", group: "agents", role: "agent with WorkIQ tool" },
    { id: "webiq", agentName: "market-evidence-expert", label: "market-evidence-expert", hostedPort: 8092, stubEndpoint: "/webiq", group: "agents", role: "agent with WebIQ tool" },
    { id: "foundryiq", agentName: "contract-policy-expert", label: "contract-policy-expert", hostedPort: 8093, stubEndpoint: "/foundryiq", group: "agents", role: "agent with FoundryIQ tool" },
    { id: "fabriciq", agentName: "operations-data-expert", label: "operations-data-expert", hostedPort: 8094, stubEndpoint: "/fabriciq", group: "agents", role: "agent with FabricIQ tool" },
].map((node) => ({
    ...node,
    port: IQ_MODE === "hosted" ? node.hostedPort : 8090,
    endpoint: IQ_MODE === "hosted" ? "/responses" : node.stubEndpoint,
}));
const PROMPT_AGENT_IDS = new Set(PROMPT_AGENT_NODES.map((n) => n.id));

const NODES = [
    { id: "waypoint-pg", label: "Postgres", port: 5433, group: "waypoint", role: "database (waypoint-pg)" },
    { id: "waypoint-api", label: "Waypoint API", port: 8000, group: "waypoint", role: "REST API boundary" },
    { id: "waypoint-web", label: "Waypoint Web", port: 5173, group: "waypoint", role: "React/Vite frontend" },
    ...(IQ_MODE === "stub"
        ? [{ id: PROMPT_VALIDATOR_NODE, label: "IQ-tool agent stub host", port: 8090, group: "agents", role: "fast deterministic agent stubs" }]
        : []),
    ...PROMPT_AGENT_NODES,
    { id: "pacioli", label: "assurance-orchestrator", port: 8088, group: "coordinator", role: "coordinator" },
];

// Run targets the UI can drive (an endpoint that serves /responses).
const RUN_TARGETS = {
    pacioli: { port: 8088, label: "assurance-orchestrator" },
};

// Local Waypoint runs on fixed, well-known ports. The canvas launches the API
// (uvicorn) and web (Vite) directly against the user's persistent local
// Postgres container so their real data is visible — rather than delegating to
// Aspire, which spins an ephemeral, separately-seeded database container.
const WAYPOINT_API_BASE = process.env.WAYPOINT_API_BASE_URL || "http://127.0.0.1:8000";
const WAYPOINT_WEB_BASE = process.env.WAYPOINT_WEB_BASE_URL || "http://127.0.0.1:5173";
const WAYPOINT_PG_PORT = Number(process.env.WAYPOINT_PG_PORT || 5433);
const WAYPOINT_PG_CONTAINER = process.env.WAYPOINT_PG_CONTAINER || "waypoint-pg";
const WAYPOINT_DB_CONNECTION = process.env.WAYPOINT_DB_CONNECTION
    || "postgresql://waypoint_app:waypoint_dev_pw@localhost:5433/waypoint";

let cachedWaypointApiBaseUrl = WAYPOINT_API_BASE;
let cachedWaypointWebBaseUrl = WAYPOINT_WEB_BASE;

const DEFAULT_PROMPT =
    "Run invoice assurance for invoice id INV-2026-08034. Select IQ-tool agents as needed, " +
    "build the canonical packet, synthesize the evidence-backed judgement, and return " +
    "the read-only Waypoint write plan.";

// One loopback HTTP server per open canvas instance.
const servers = new Map(); // instanceId -> { server, url }
const runJobs = new Map(); // jobId -> { status, events, result, error, startedAt, updatedAt }
const commandHistory = [];
const canvasEvents = [];
let nextRunJobId = 1;

function rememberCanvasEvent(event) {
    canvasEvents.unshift({
        ts: Date.now(),
        ...event,
    });
    if (canvasEvents.length > 80) canvasEvents.length = 80;
}

function pruneRunJobs() {
    const jobs = [...runJobs.entries()].sort((a, b) => (b[1].updatedAt || 0) - (a[1].updatedAt || 0));
    for (const [id] of jobs.slice(8)) runJobs.delete(id);
}

function addRunEvent(job, step, detail, node) {
    const event = {
        ts: Date.now(),
        elapsedMs: Date.now() - job.startedAt,
        step,
        detail,
        node: node || null,
    };
    job.step = step;
    job.updatedAt = event.ts;
    job.events.push(event);
    if (job.events.length > 60) job.events.shift();
    return event;
}

function publicRunJob(job) {
    return {
        id: job.id,
        target: job.target,
        status: job.status,
        step: job.step,
        startedAt: job.startedAt,
        updatedAt: job.updatedAt,
        elapsedMs: Date.now() - job.startedAt,
        events: job.events,
        result: job.result || null,
        error: job.error || null,
    };
}

function rememberCommand(entry) {
    commandHistory.unshift({
        ts: Date.now(),
        ...entry,
    });
    if (commandHistory.length > 20) commandHistory.length = 20;
}

function startRunJob(target, message, options = {}) {
    pruneRunJobs();
    const job = {
        id: `run-${Date.now()}-${nextRunJobId++}`,
        target,
        status: "running",
        step: "queued",
        startedAt: Date.now(),
        updatedAt: Date.now(),
        events: [],
        result: null,
        error: null,
    };
    runJobs.set(job.id, job);
    addRunEvent(job, "queued", "Canvas accepted the run and started live tracking.", "pacioli");
    runPipeline(target, message, options, (step, detail, node) => addRunEvent(job, step, detail, node))
        .then((result) => {
            job.status = "completed";
            job.result = result;
            addRunEvent(job, "completed", "Pacioli returned a completed response.", "pacioli");
        })
        .catch((err) => {
            job.status = "error";
            job.error = err instanceof CanvasError ? err.message : String(err?.message || err);
            addRunEvent(job, "error", job.error, "pacioli");
        });
    return publicRunJob(job);
}

// ---------------------------------------------------------------------------
// Probes
// ---------------------------------------------------------------------------
function checkPort(port) {
    return new Promise((resolve) => {
        const sock = new net.Socket();
        let settled = false;
        const finish = (up) => {
            if (settled) return;
            settled = true;
            sock.destroy();
            resolve(up);
        };
        sock.setTimeout(700);
        sock.once("connect", () => finish(true));
        sock.once("timeout", () => finish(false));
        sock.once("error", () => finish(false));
        sock.connect(port, "127.0.0.1");
    });
}

async function fetchJson(url, opts = {}, timeoutMs = 4000) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        const res = await fetch(url, { ...opts, signal: ctrl.signal });
        const text = await res.text();
        let body;
        try {
            body = text ? JSON.parse(text) : null;
        } catch {
            body = text;
        }
        return { ok: res.ok, status: res.status, body };
    } finally {
        clearTimeout(t);
    }
}

function recentAspireUrls() {
    const urls = new Set();
    const logsDir = process.env.USERPROFILE
        ? path.join(process.env.USERPROFILE, ".aspire", "logs")
        : path.join(process.env.HOME || "", ".aspire", "logs");
    try {
        const files = fs.readdirSync(logsDir)
            .map((name) => path.join(logsDir, name))
            .filter((file) => fs.statSync(file).isFile())
            .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)
            .slice(0, 6);
        for (const file of files) {
            const text = fs.readFileSync(file, "utf8");
            for (const match of text.matchAll(/https?:\/\/localhost:\d+/g)) urls.add(match[0]);
        }
    } catch {
        /* Aspire may not be installed or started yet. */
    }
    return [...urls];
}

async function waypointApiReachable(baseUrl, timeoutMs = 2500) {
    if (!baseUrl) return false;
    const normalized = baseUrl.replace(/\/+$/, "");
    const result = await fetchJson(`${normalized}/api/cases`, {}, timeoutMs).catch(() => null);
    return Boolean(result?.ok && Array.isArray(result.body));
}

async function describeAspireResourceUrl(resourceName) {
    // Ask Aspire for the live URL of a named AppHost resource (e.g. "api" or
    // "web"). Aspire assigns dynamic ports in local run mode, so this is the
    // authoritative way to learn where a resource is actually listening.
    const appHostDir = resolveWaypointAppHostDir();
    if (!appHostDir) return "";
    const output = await execText(
        resolveBin("aspire"),
        ["describe", resourceName, "--apphost", appHostDir, "--format", "Json", "--non-interactive", "--nologo"],
        { cwd: appHostDir, env: augmentedEnv(), windowsHide: true },
    );
    const jsonStart = output.indexOf("{");
    if (jsonStart < 0) return "";
    try {
        const data = JSON.parse(output.slice(jsonStart));
        const resources = Array.isArray(data.resources) ? data.resources : [];
        const match = resources.find((r) =>
            r.displayName === resourceName || String(r.name || "").startsWith(resourceName + "-"));
        const urls = Array.isArray(match?.urls) ? match.urls : [];
        const http = urls.find((u) => /^https?:\/\//.test(String(u.url || "")));
        return http?.url ? String(http.url).replace(/\/+$/, "") : "";
    } catch {
        return "";
    }
}

async function describeAspireWaypointApiUrl() {
    return describeAspireResourceUrl("api");
}

async function waypointWebReachable(baseUrl, timeoutMs = 2500) {
    if (!baseUrl) return false;
    const normalized = baseUrl.replace(/\/+$/, "");
    const result = await fetchJson(`${normalized}/`, {}, timeoutMs).catch(() => null);
    return Boolean(result?.ok);
}

async function resolveWaypointWebBaseUrl({ discover = false, timeoutMs = 1500 } = {}) {
    // The web URL is resolved authoritatively from `aspire describe web` rather
    // than scraping recent Aspire log URLs: both the api and web roots return
    // 200, so a log-scraped candidate could mistake the API port for the
    // frontend. env override and the last-known-good cache are checked first to
    // keep steady-state refreshes fast.
    const cheapCandidates = [
        process.env.WAYPOINT_WEB_BASE_URL,
        cachedWaypointWebBaseUrl,
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ].filter(Boolean);
    for (const candidate of cheapCandidates) {
        const normalized = candidate.replace(/\/+$/, "");
        if (await waypointWebReachable(normalized, timeoutMs)) {
            cachedWaypointWebBaseUrl = normalized;
            return normalized;
        }
    }
    if (discover) {
        const aspireWebUrl = await describeAspireResourceUrl("web").catch(() => "");
        if (aspireWebUrl) {
            const normalized = aspireWebUrl.replace(/\/+$/, "");
            if (await waypointWebReachable(normalized, timeoutMs)) {
                cachedWaypointWebBaseUrl = normalized;
                return normalized;
            }
        }
    }
    return process.env.WAYPOINT_WEB_BASE_URL || cachedWaypointWebBaseUrl || "";
}

async function resolveWaypointApiBaseUrl({ discover = false, includeRecent = false, timeoutMs = 500 } = {}) {
    // Try the cheap candidates first (env, last-known-good cache, the well-known
    // :8000 default, and any localhost URLs scraped from recent Aspire logs).
    // Aspire assigns the API a DYNAMIC port in local run mode, so the static
    // :8000 fallback rarely matches — the cache and the log-scraped URLs are
    // what make a plain refresh find a running Aspire-managed Waypoint.
    const cheapCandidates = [
        process.env.WAYPOINT_API_BASE_URL,
        cachedWaypointApiBaseUrl,
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        ...(includeRecent ? recentAspireUrls() : []),
    ].filter(Boolean);
    for (const candidate of cheapCandidates) {
        const normalized = candidate.replace(/\/+$/, "");
        if (await waypointApiReachable(normalized, timeoutMs)) {
            cachedWaypointApiBaseUrl = normalized;
            return normalized;
        }
    }
    // Only pay for `aspire describe` (slow) when the cheap candidates all miss.
    if (discover) {
        const aspireApiUrl = await describeAspireWaypointApiUrl().catch(() => "");
        if (aspireApiUrl) {
            const normalized = aspireApiUrl.replace(/\/+$/, "");
            if (await waypointApiReachable(normalized, timeoutMs)) {
                cachedWaypointApiBaseUrl = normalized;
                return normalized;
            }
        }
    }
    return process.env.WAYPOINT_API_BASE_URL || cachedWaypointApiBaseUrl || "http://127.0.0.1:8000";
}

async function buildState() {
    const waypointApiBaseUrl = WAYPOINT_API_BASE;
    const waypointWebBaseUrl = WAYPOINT_WEB_BASE;
    const promptHealth = IQ_MODE === "stub"
        ? await fetchJson("http://127.0.0.1:8090/healthz", {}, 1200).catch(() => null)
        : null;
    const promptAgents = new Set(Array.isArray(promptHealth?.body?.agents) ? promptHealth.body.agents : []);
    const promptMode = promptHealth?.body?.mode || (promptHealth?.ok ? "mode unknown" : "down");
    const nodes = await Promise.all(NODES.map(async (n) => {
        if (n.id === PROMPT_VALIDATOR_NODE) {
            return { ...n, up: Boolean(promptHealth?.ok), detail: promptHealth?.ok ? `${promptMode} mode` : "healthz down" };
        }
        if (IQ_MODE === "stub" && n.agentName) {
            const up = Boolean(promptHealth?.ok && promptAgents.has(n.agentName));
            return { ...n, up, detail: up ? `:8090${n.endpoint}` : "not registered" };
        }
        if (n.id === "waypoint-pg") {
            const up = await checkPort(WAYPOINT_PG_PORT);
            return { ...n, up, detail: up ? `localhost:${WAYPOINT_PG_PORT}/waypoint` : `:${WAYPOINT_PG_PORT}` };
        }
        if (n.id === "waypoint-api") {
            return {
                ...n,
                up: await waypointApiReachable(waypointApiBaseUrl),
                detail: waypointApiBaseUrl,
            };
        }
        if (n.id === "waypoint-web") {
            const up = await waypointWebReachable(waypointWebBaseUrl);
            return {
                ...n,
                up,
                detail: up ? waypointWebBaseUrl : "not started",
            };
        }
        const up = await checkPort(n.port);
        return { ...n, up, detail: n.endpoint ? `:${n.port}${n.endpoint}` : `:${n.port}` };
    }));
    const apiUp = nodes.find((n) => n.id === "waypoint-api")?.up;

    let waypoint = { reachable: false, cases: [], runs: 0, audit: [] };
    if (apiUp) {
        try {
            const [cases, runs, audit] = await Promise.all([
                fetchJson(`${waypointApiBaseUrl}/api/cases`),
                fetchJson(`${waypointApiBaseUrl}/api/runs`),
                fetchJson(`${waypointApiBaseUrl}/api/audit/decisions`),
            ]);
            waypoint = {
                reachable: true,
                baseUrl: waypointApiBaseUrl,
                cases: Array.isArray(cases.body) ? cases.body : [],
                runs: Array.isArray(runs.body) ? runs.body.length : 0,
                audit: Array.isArray(audit.body) ? audit.body : [],
            };
        } catch {
            waypoint = { reachable: false, cases: [], runs: 0, audit: [] };
        }
    }
    return { nodes, waypoint, ts: Date.now() };
}

// ---------------------------------------------------------------------------
// Run a pipeline pass against a /responses endpoint and parse the fan-out.
// ---------------------------------------------------------------------------
function mapNode(name) {
    const lc = String(name || "").toLowerCase();
    for (const key of PROMPT_AGENT_IDS) {
        if (lc.includes(key)) return key;
    }
    return null;
}

function touchValidatorNodes(workflow, touched) {
    let found = false;
    const candidates = [];
    if (Array.isArray(workflow?.validators)) candidates.push(...workflow.validators);
    if (Array.isArray(workflow?.validator_routing?.selected_validators)) {
        candidates.push(...workflow.validator_routing.selected_validators);
    }
    for (const candidate of candidates) {
        const id = typeof candidate === "string" ? candidate : candidate?.validator_id;
        const node = mapNode(id);
        if (node) {
            touched.add(node);
            found = true;
        }
    }
}

function trunc(value, max = 400) {
    const s = typeof value === "string" ? value : JSON.stringify(value);
    if (s == null) return "";
    return s.length > max ? s.slice(0, max) + "…" : s;
}

function parseOutput(data) {
    const items = [];
    const touched = new Set();
    const candidateTexts = [];
    if (typeof data?.output_text === "string") candidateTexts.push(data.output_text);
    for (const o of (data && data.output) || []) {
        const type = o.type;
        if (type === "function_call") {
            const node = mapNode(o.name);
            if (node) touched.add(node);
            items.push({ kind: "call", name: o.name, node, text: trunc(o.arguments, 600) });
        } else if (type === "function_call_output") {
            const text = typeof o.output === "string" ? o.output : JSON.stringify(o.output ?? o.result);
            candidateTexts.push(text);
            items.push({ kind: "output", text: trunc(o.output ?? o.result, 600) });
        } else if (type === "message") {
            const text = Array.isArray(o.content)
                ? o.content.map((c) => c.text || "").join("")
                : o.content || "";
            if (text) candidateTexts.push(text);
            items.push({ kind: "message", text: trunc(text, 800) });
        } else if (type === "reasoning") {
            items.push({ kind: "reasoning", text: "(model reasoning)" });
        } else {
            items.push({ kind: type || "unknown", text: "" });
        }
    }
    const workflow = candidateTexts.map(extractJsonObject).find((value) => value && value.write_plan);
    if (workflow) {
        touched.add("pacioli");
        touchValidatorNodes(workflow, touched);
        if (workflow.write_plan) touched.add("waypoint-api");
        if (!items.length) {
            items.push({
                kind: "message",
                text: trunc(
                    `Pacioli completed ${workflow.validators?.length || 0} IQ-tool agent(s); write plan read_only=${workflow.write_plan?.read_only}.`,
                    800,
                ),
            });
        }
    }
    return { items, touched: [...touched], workflow };
}

function extractJsonObject(text) {
    if (!text || typeof text !== "string") return null;
    const trimmed = text.trim();
    const candidates = [trimmed];
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start >= 0 && end > start) candidates.push(trimmed.slice(start, end + 1));
    for (const candidate of candidates) {
        try {
            const parsed = JSON.parse(candidate);
            if (parsed && typeof parsed === "object") return parsed;
        } catch {
            /* try next candidate */
        }
    }
    return null;
}

function startWaypointRunMonitor(jobProgress, waypointApiBaseUrl, beforeRuns) {
    if (!jobProgress || !waypointApiBaseUrl) return null;
    let lastRuns = beforeRuns;
    const tick = async () => {
        const snapshot = await fetchJson(`${waypointApiBaseUrl}/api/runs`, {}, 2000).catch(() => null);
        const count = Array.isArray(snapshot?.body) ? snapshot.body.length : lastRuns;
        if (count > lastRuns) {
            lastRuns = count;
            jobProgress("waypoint_write_seen", `Waypoint run count increased to ${count}.`, "waypoint-api");
        }
    };
    return setInterval(() => {
        void tick();
    }, 2500);
}

function buildPacioliInput(message, options = {}) {
    const request = buildPacioliWorkflowRequest(options);
    if (!request.items[0].pdf_uri && !request.items[0].pdf_base64) return message;
    return [
        message,
        "",
        "Call pacioli_run_invoice_assurance_workflow with this exact request_json payload:",
        JSON.stringify(request),
    ].join("\n");
}

function buildPacioliWorkflowRequest(options = {}) {
    const pdfUri = String(options.pdfUri || options.pdf_uri || "").trim();
    const pdfBase64 = String(options.pdfBase64 || options.pdf_base64 || "").trim();
    return {
        mode: "run",
        request_type: "invoice_assurance",
        batch_id: options.batchId || options.batch_id || "pacioli-canvas-pdf",
        source: "pipeline-control-canvas",
        items: [
            {
                invoice_id: options.invoiceId || options.invoice_id || "INV-2026-08034",
                pdf_uri: pdfUri || undefined,
                pdf_base64: pdfBase64 || undefined,
            },
        ],
        constraints: { read_only: true, allowed_write_phase: "none" },
    };
}

async function runPipeline(target, message, options = {}, jobProgress = null) {
    const tgt = RUN_TARGETS[target] || RUN_TARGETS.pacioli;
    const endpoint = `http://localhost:${tgt.port}/responses`;
    jobProgress?.("resolve_waypoint", "Resolving the Waypoint API boundary.", "waypoint-api");
    const waypointApiBaseUrl = await resolveWaypointApiBaseUrl();

    jobProgress?.("check_pacioli", `Checking ${tgt.label} on :${tgt.port}.`, "pacioli");
    const pacioliEndpointUp = await checkPort(tgt.port);

    jobProgress?.("snapshot_before", "Reading current Waypoint run count before execution.", "waypoint-api");
    const before = await fetchJson(`${waypointApiBaseUrl}/api/runs`).catch(() => ({ body: [] }));
    const beforeRuns = Array.isArray(before.body) ? before.body.length : 0;

    const started = Date.now();
    const monitor = startWaypointRunMonitor(jobProgress, waypointApiBaseUrl, beforeRuns);
    let res;
    let executionPath = "responses";
    try {
        if (pacioliEndpointUp) {
            jobProgress?.("post_responses", `POST ${endpoint}; Pacioli is running the deterministic workflow.`, "pacioli");
            res = await fetchJson(
                endpoint,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ input: buildPacioliInput(message, options), store: false }),
                },
                240000,
            );
        } else {
            executionPath = "local_workflow";
            jobProgress?.(
                "run_local_workflow",
                `Pacioli /responses is down on :${tgt.port}; running the deterministic workflow directly.`,
                "pacioli",
            );
            res = await runPacioliWorkflowLocally(options, waypointApiBaseUrl);
        }
    } catch (err) {
        throw new CanvasError("run_failed", `Pacioli run failed: ${err?.message || err}`);
    } finally {
        if (monitor) clearInterval(monitor);
    }
    const elapsedMs = Date.now() - started;

    jobProgress?.("snapshot_after_pacioli", "Reading Waypoint run count after Pacioli returned.", "waypoint-api");
    const afterPacioli = await fetchJson(`${waypointApiBaseUrl}/api/runs`).catch(() => ({ body: [] }));
    const afterPacioliRuns = Array.isArray(afterPacioli.body) ? afterPacioli.body.length : 0;

    jobProgress?.("parse_response", "Parsing Pacioli response, IQ-tool agent fan-out, and write-plan preview.", "pacioli");
    const parsed = parseOutput(res.body);
    const workflow = parsed.workflow || null;
    if (parsed.touched?.length) {
        jobProgress?.("fanout_observed", `Observed workflow touches: ${parsed.touched.join(", ")}.`, "pacioli");
    }

    const aggregatorHandoff = await handoffWritePlanToAggregator(workflow, waypointApiBaseUrl, jobProgress);
    const after = await fetchJson(`${waypointApiBaseUrl}/api/runs`).catch(() => ({ body: [] }));
    const afterRuns = Array.isArray(after.body) ? after.body.length : afterPacioliRuns;

    return {
        endpoint,
        waypointApiBaseUrl,
        target,
        httpStatus: res.status,
        executionPath,
        elapsedMs,
        runDelta: afterRuns - beforeRuns,
        pacioliRunDelta: afterPacioliRuns - beforeRuns,
        aggregatorRunDelta: afterRuns - afterPacioliRuns,
        aggregatorHandoff,
        readOnly: workflow?.write_plan?.read_only === true,
        sideEffectsPerformed: workflow?.side_effects_performed === true,
        validatorCount: Array.isArray(workflow?.validators) ? workflow.validators.length : null,
        futurePayloadCount: Array.isArray(workflow?.write_plan?.future_payloads)
            ? workflow.write_plan.future_payloads.length
            : null,
        ...parsed,
    };
}

async function runPacioliWorkflowLocally(options = {}, waypointApiBaseUrl) {
    const pacioliDir = path.join(WORKSPACE_PATH, "agents", "assurance-orchestrator");
    const requestFile = path.join(os.tmpdir(), `pipeline-assurance-orchestrator-request-${Date.now()}.json`);
    fs.writeFileSync(requestFile, JSON.stringify(buildPacioliWorkflowRequest(options)), "utf8");
    const script = [
        "import json, sys",
        "from assurance_workflow import run_assurance_orchestrator_invoice_assurance",
        "with open(sys.argv[1], encoding='utf-8') as f:",
        "    request = json.load(f)",
        "print(json.dumps(run_assurance_orchestrator_invoice_assurance(request)))",
    ].join("\n");
    try {
        const result = await execCapture(
            resolveBin("uv"),
            ["run", "--frozen", "python", "-c", script, requestFile],
            {
                cwd: pacioliDir,
                env: augmentedEnv({
                    WAYPOINT_API_BASE_URL: waypointApiBaseUrl,
                    WAYPOINT_API_VERIFY_SSL: "false",
                    WORKIQ_EXPERT_ENDPOINT: "http://127.0.0.1:8090/workiq",
                    WEBIQ_EXPERT_ENDPOINT: "http://127.0.0.1:8090/webiq",
                    FOUNDRYIQ_EXPERT_ENDPOINT: "http://127.0.0.1:8090/foundryiq",
                    FABRICIQ_EXPERT_ENDPOINT: "http://127.0.0.1:8090/fabriciq",
                    UV_LINK_MODE: process.env.UV_LINK_MODE || "copy",
                }),
                timeout: 240000,
                windowsHide: true,
            },
        );
        if (!result.ok) {
            throw new Error(result.stderr || result.error || "local Pacioli workflow failed");
        }
        const workflow = extractJsonObject(result.stdout);
        if (!workflow?.write_plan) {
            throw new Error("local Pacioli workflow returned no write_plan");
        }
        return {
            ok: true,
            status: 200,
            body: { output_text: JSON.stringify(workflow), output: [] },
        };
    } finally {
        try {
            fs.unlinkSync(requestFile);
        } catch {
            /* best-effort temp cleanup */
        }
    }
}

async function handoffWritePlanToAggregator(workflow, waypointApiBaseUrl, jobProgress = null) {
    const futurePayloads = workflow?.write_plan?.future_payloads;
    if (!Array.isArray(futurePayloads) || futurePayloads.length === 0) {
        return { attempted: false, ok: false, reason: "Pacioli returned no write_plan.future_payloads." };
    }
    if (futurePayloads.length !== 1) {
        return {
            attempted: true,
            ok: false,
            error: `Expected exactly one future payload for local aggregator handoff, got ${futurePayloads.length}.`,
        };
    }

    const aggregatorDir = path.join(WORKSPACE_PATH, "agents", "waypoint-recorder");
    const payloadFile = path.join(os.tmpdir(), `pipeline-waypoint-recorder-handoff-${Date.now()}.json`);
    const payload = {
        read_only: workflow?.write_plan?.read_only === true,
        side_effects_performed: workflow?.write_plan?.side_effects_performed === true,
        future_payloads: [futurePayloads[0]],
    };
    fs.writeFileSync(payloadFile, JSON.stringify(payload), "utf8");
    jobProgress?.("handoff_to_aggregator", "Handing Pacioli write-plan preview to the local aggregator write boundary.", "waypoint-api");

    const script = [
        "import json, sys",
        "from waypoint_write_tools import _waypoint_record_assurance_inner",
        "with open(sys.argv[1], encoding='utf-8') as f:",
        "    payload = f.read()",
        "print(_waypoint_record_assurance_inner(payload))",
    ].join("\n");
    try {
        const result = await execCapture(
            resolveBin("uv"),
            ["run", "--frozen", "python", "-c", script, payloadFile],
            {
                cwd: aggregatorDir,
                env: augmentedEnv({
                    WAYPOINT_API_BASE_URL: waypointApiBaseUrl,
                    WAYPOINT_API_VERIFY_SSL: "false",
                    UV_LINK_MODE: process.env.UV_LINK_MODE || "copy",
                }),
                timeout: 120000,
                windowsHide: true,
            },
        );
        const parsed = extractJsonObject(result.stdout);
        const ok = Boolean(result.ok && parsed?.ok === true);
        jobProgress?.(
            ok ? "aggregator_write_completed" : "aggregator_write_failed",
            ok ? "Aggregator wrote the Waypoint artifacts." : (parsed?.error || result.stderr || result.error || "Aggregator handoff failed."),
            "waypoint-api",
        );
        return {
            attempted: true,
            ok,
            status: result.ok ? "completed" : "failed",
            correlation: parsed?.correlation || null,
            decision: parsed?.decision || null,
            error: ok ? null : (parsed?.error || result.stderr || result.error || "Aggregator handoff failed."),
        };
    } finally {
        try {
            fs.unlinkSync(payloadFile);
        } catch {
            /* best-effort temp cleanup */
        }
    }
}

// ---------------------------------------------------------------------------
// Process orchestration — spin Forge-local services up with one click. Every
// service is spawned DETACHED (unref'd) so it survives this canvas/session
// teardown. Waypoint is Aspire-managed in the Waypoint repo; this canvas only
// observes its API boundary.
// ---------------------------------------------------------------------------
const LOCAL_BIN_DIRS = [
    ...(process.platform === "win32"
        ? [
              process.env.LOCALAPPDATA ? `${process.env.LOCALAPPDATA}\\Programs\\Azure Dev CLI` : "",
              process.env.ProgramFiles ? `${process.env.ProgramFiles}\\Microsoft SDKs\\Azure\\Azure Dev CLI` : "",
              process.env.USERPROFILE ? `${process.env.USERPROFILE}\\.azd\\bin` : "",
              process.env.USERPROFILE ? `${process.env.USERPROFILE}\\.dotnet\\tools` : "",
              process.env.USERPROFILE ? `${process.env.USERPROFILE}\\.local\\bin` : "",
              process.env.ProgramFiles ? `${process.env.ProgramFiles}\\dotnet` : "",
              // Node.js (for `npm` when launching the Waypoint web dev server).
              process.env.ProgramFiles ? `${process.env.ProgramFiles}\\nodejs` : "",
              process.env["ProgramFiles(x86)"] ? `${process.env["ProgramFiles(x86)"]}\\nodejs` : "",
              process.env.APPDATA ? `${process.env.APPDATA}\\npm` : "",
              process.env.SystemRoot ? `${process.env.SystemRoot}\\System32` : "",
              process.env.SystemRoot || "",
          ]
        : [
              "/opt/homebrew/bin",
              `${process.env.HOME}/.azd/bin`,
              `${process.env.HOME}/.dotnet/tools`,
              `${process.env.HOME}/.local/bin`,
              "/usr/local/share/dotnet",
              "/usr/local/bin",
              "/usr/bin",
              "/usr/sbin",
              "/bin",
              "/sbin",
          ]),
];

function augmentedEnv(extra) {
    const dirs = [...LOCAL_BIN_DIRS, ...((process.env.PATH || "").split(path.delimiter))];
    const seen = new Set();
    const pathValue = dirs.filter((d) => d && !seen.has(d) && seen.add(d)).join(path.delimiter);
    return { ...process.env, ...(extra || {}), PATH: pathValue };
}

function resolveBin(name, alts = []) {
    const baseNames = [name, ...alts];
    const names =
        process.platform === "win32"
            ? baseNames.flatMap((n) => [n, `${n}.cmd`, `${n}.exe`])
            : baseNames;
    for (const d of LOCAL_BIN_DIRS) {
        for (const candidate of names) {
            const p = path.join(d, candidate);
            try {
                if (fs.existsSync(p)) return p;
            } catch {
                /* ignore */
            }
        }
    }
    return name;
}

function resolvePython(preferred) {
    if (preferred) {
        try {
            if (fs.existsSync(preferred)) return preferred;
        } catch {
            /* ignore */
        }
    }
    const candidates = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
    for (const candidate of candidates) {
        const resolved = resolveBin(candidate);
        if (resolved !== candidate) return resolved;
    }
    return candidates[0];
}

// Read the checked-out branch of a git working tree WITHOUT spawning git (the
// canvas process has a minimal PATH under the GUI-app launch). Handles both a
// primary checkout (<dir>/.git is a directory) and a linked worktree
// (<dir>/.git is a file pointing at the real gitdir). Returns the branch name,
// or "" when detached/unknown.
function gitBranchOf(dir) {
    try {
        const dotGit = path.join(dir, ".git");
        const st = fs.statSync(dotGit);
        let headFile;
        if (st.isDirectory()) {
            headFile = path.join(dotGit, "HEAD");
        } else {
            const ref = fs.readFileSync(dotGit, "utf8").trim();
            const m = ref.match(/^gitdir:\s*(.+)$/);
            if (!m) return "";
            const gitDir = path.isAbsolute(m[1]) ? m[1] : path.resolve(dir, m[1]);
            headFile = path.join(gitDir, "HEAD");
        }
        const head = fs.readFileSync(headFile, "utf8").trim();
        const branch = head.match(/^ref:\s*refs\/heads\/(.+)$/);
        return branch ? branch[1].trim() : "";
    } catch {
        return "";
    }
}

// Locate the Waypoint AppHost directory (the checkout that contains apphost.cs).
// Resolution order:
//   1. WAYPOINT_DIR env override (explicit wins).
//   2. The worktree checked out to `main` — we want the canvas to drive the
//      mainline Waypoint code, not whatever feature branch happens to live in
//      the primary ~/git/waypoint checkout.
//   3. Any other checkout that has apphost.cs (first found).
function resolveWaypointAppHostDir() {
    if (process.env.WAYPOINT_DIR && hasAppHost(process.env.WAYPOINT_DIR)) {
        return process.env.WAYPOINT_DIR;
    }
    const candidates = [];
    const roots = process.platform === "win32"
        ? [
              process.env.USERPROFILE ? path.join(process.env.USERPROFILE, ".copilot", "repos", "waypoint") : "",
              process.env.USERPROFILE ? path.join(process.env.USERPROFILE, ".copilot", "copilot-worktrees", "waypoint") : "",
          ]
        : [
              `${process.env.HOME}/git/waypoint`,
              `${process.env.HOME}/git/copilot-worktrees/waypoint`,
          ];
    for (const root of roots) {
        if (!root) continue;
        candidates.push(root);
        try {
            for (const d of fs.readdirSync(root)) candidates.push(path.join(root, d));
        } catch {
            /* ignore */
        }
    }
    const appHostDirs = candidates.filter((c) => hasAppHost(c));
    if (!appHostDirs.length) return null;
    const mainWorktree = appHostDirs.find((c) => gitBranchOf(c) === "main");
    return mainWorktree || appHostDirs[0];
}

function hasAppHost(dir) {
    try {
        return Boolean(dir) && fs.existsSync(path.join(dir, "apphost.cs"));
    } catch {
        return false;
    }
}

const STACK_AGENTS = [
    { name: "collaboration-evidence-expert", port: 8091, id: "workiq" },
    { name: "market-evidence-expert", port: 8092, id: "webiq" },
    { name: "contract-policy-expert", port: 8093, id: "foundryiq" },
    { name: "operations-data-expert", port: 8094, id: "fabriciq" },
    { name: "assurance-orchestrator", port: 8088, id: "pacioli" },
];

// Local-only speed overrides for the spun-up stack. These are injected ONLY into
// the locally-launched agents below — they do NOT touch agent.yaml or any
// deployed Foundry run. Override at launch time via the environment if desired:
//   PIPELINE_LOCAL_MODEL          faster chat model deployment for every agent
//   PIPELINE_WEBIQ_CONTEXT_SIZE   Bing grounding context size for webiq (low/medium/high)
//   PIPELINE_FOUNDRY_ENDPOINT     Foundry project endpoint for every agent
const LOCAL_WEBIQ_CONTEXT_SIZE = process.env.PIPELINE_WEBIQ_CONTEXT_SIZE || "low";
const LAUNCH_ENV_KEYS = [
    "TOOLBOX_ENDPOINT",
    "TOOLBOX_MCP_ENDPOINT",
    "WORKIQ_EMAIL_MCP_SERVER_URL",
    "WORKIQ_TEAMS_MCP_SERVER_URL",
    "WORKIQ_SHAREPOINT_MCP_SERVER_URL",
    "WORKIQ_MCP_SERVER_URL",
    "WORKIQ_MCP_SCOPE",
    "WORKIQ_MCP_API_KEY",
    "WORKIQ_MCP_API_KEY_HEADER",
    "WORKIQ_MCP_API_KEY_SCHEME",
    "CONTENT_UNDERSTANDING_ENDPOINT",
    "CONTENT_UNDERSTANDING_API_VERSION",
    "CONTENT_UNDERSTANDING_ANALYZER_ID",
    "CONTENT_UNDERSTANDING_SCOPE",
    "CONTENT_UNDERSTANDING_TIMEOUT_SECONDS",
    "CONTENT_UNDERSTANDING_POLL_INTERVAL_SECONDS",
    "CONTENT_UNDERSTANDING_MAX_POLLS",
];

const AGENT_IDS = new Set(STACK_AGENTS.map((a) => a.id));

// Minimal dotenv parser for azd's `.azure/<env>/.env` value file.
function parseDotenv(text) {
    const out = {};
    for (const line of text.split(/\r?\n/)) {
        const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
        if (!m) continue;
        let value = m[2];
        if (
            (value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))
        ) {
            value = value.slice(1, -1);
        }
        out[m[1]] = value;
    }
    return out;
}

// Locate the azd `.azure` directory. The spin-up runs from the worktree, which
// has no `.azure`, so fall back to the main checkout discovered via the
// worktree's `.git` pointer file.
function findAzureDir(forgeDir) {
    const here = path.join(forgeDir, ".azure");
    try {
        if (fs.existsSync(here)) return here;
    } catch {
        /* ignore */
    }
    try {
        const gitPath = path.join(forgeDir, ".git");
        if (fs.statSync(gitPath).isFile()) {
            const m = fs.readFileSync(gitPath, "utf8").match(/gitdir:\s*(.+)/);
            if (m) {
                const gitDir = path.resolve(forgeDir, m[1].trim());
                const marker = `${path.sep}.git`;
                const idx = gitDir.indexOf(marker);
                const mainRoot = idx >= 0 ? gitDir.slice(0, idx) : path.dirname(path.dirname(gitDir));
                const cand = path.join(mainRoot, ".azure");
                if (fs.existsSync(cand)) return cand;
            }
        }
    } catch {
        /* ignore */
    }
    return null;
}

// Read the active azd environment's value file so spin-up picks up
// AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_MODEL_DEPLOYMENT_NAME without an app
// restart (the running extension's process.env is fixed at launch).
function readAzdValues(forgeDir) {
    const azureDir = findAzureDir(forgeDir);
    if (!azureDir) return {};
    try {
        let envName = "";
        try {
            const cfg = JSON.parse(fs.readFileSync(path.join(azureDir, "config.json"), "utf8"));
            envName = cfg.defaultEnvironment || "";
        } catch {
            /* fall through to directory scan */
        }
        if (!envName) {
            for (const d of fs.readdirSync(azureDir)) {
                if (fs.existsSync(path.join(azureDir, d, ".env"))) {
                    envName = d;
                    break;
                }
            }
        }
        if (!envName) return {};
        const envFile = path.join(azureDir, envName, ".env");
        if (!fs.existsSync(envFile)) return {};
        return parseDotenv(fs.readFileSync(envFile, "utf8"));
    } catch {
        return {};
    }
}

function readRepoEnvValues(forgeDir) {
    const envFile = path.join(forgeDir, ".env");
    try {
        if (!fs.existsSync(envFile)) return {};
        return parseDotenv(fs.readFileSync(envFile, "utf8"));
    } catch {
        return {};
    }
}

function execText(cmd, args, opts = {}) {
    return new Promise((resolve) => {
        execFile(cmd, args, opts, (err, stdout) => {
            if (err) return resolve("");
            resolve(String(stdout || "").trim());
        });
    });
}

async function readAzdValueCli(forgeDir, key) {
    const envName = process.env.PIPELINE_AZD_ENV_NAME || process.env.AZURE_ENV_NAME || "forge";
    return execText(resolveBin("azd"), ["env", "get-value", key, "--environment", envName, "--no-prompt"], {
        cwd: forgeDir,
        env: augmentedEnv(),
        windowsHide: true,
    });
}

// Resolve the config the agents need to launch: process.env overrides win, then
// the azd env file / CLI. Computed at spin-up time, not module load.
async function resolveLaunchConfig(forgeDir) {
    const repoEnv = readRepoEnvValues(forgeDir);
    const azd = readAzdValues(forgeDir);
    const mergedEnv = {};
    for (const key of LAUNCH_ENV_KEYS) {
        const value = process.env[key] || repoEnv[key] || azd[key];
        if (value) mergedEnv[key] = value;
    }
    for (const key of LAUNCH_ENV_KEYS) {
        if (mergedEnv[key]) continue;
        const value = await readAzdValueCli(forgeDir, key);
        if (value) mergedEnv[key] = value;
    }
    const endpoint =
        process.env.PIPELINE_FOUNDRY_ENDPOINT ||
        process.env.FOUNDRY_PROJECT_ENDPOINT ||
        process.env.AZURE_AI_PROJECT_ENDPOINT ||
        repoEnv.PIPELINE_FOUNDRY_ENDPOINT ||
        repoEnv.FOUNDRY_PROJECT_ENDPOINT ||
        repoEnv.AZURE_AI_PROJECT_ENDPOINT ||
        azd.FOUNDRY_PROJECT_ENDPOINT ||
        azd.AZURE_AI_PROJECT_ENDPOINT ||
        await readAzdValueCli(forgeDir, "AZURE_AI_PROJECT_ENDPOINT") ||
        await readAzdValueCli(forgeDir, "FOUNDRY_PROJECT_ENDPOINT") ||
        "";
    const model =
        process.env.PIPELINE_LOCAL_MODEL ||
        process.env.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        repoEnv.PIPELINE_LOCAL_MODEL ||
        repoEnv.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        azd.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        await readAzdValueCli(forgeDir, "AZURE_AI_MODEL_DEPLOYMENT_NAME") ||
        "";
    return {
        endpoint,
        model,
        env: mergedEnv,
        webiqContext: LOCAL_WEBIQ_CONTEXT_SIZE,
        waypointApiBaseUrl: await resolveWaypointApiBaseUrl({ discover: true, includeRecent: true, timeoutMs: 2500 }),
    };
}

// Per-agent local speed + required-config overrides, layered on augmentedEnv().
function agentSpeedEnv(id, cfg) {
    const extra = { ...(cfg.env || {}) };
    if (cfg.endpoint) extra.FOUNDRY_PROJECT_ENDPOINT = cfg.endpoint;
    if (cfg.model) extra.AZURE_AI_MODEL_DEPLOYMENT_NAME = cfg.model;
    if (id === "pacioli") {
        extra.UV_PYTHON = process.env.PIPELINE_UV_PYTHON || process.env.UV_PYTHON || "3.12";
        extra.UV_LINK_MODE = process.env.UV_LINK_MODE || "copy";
        if (IQ_MODE === "hosted") {
            extra.WORKIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8091";
            extra.WEBIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8092";
            extra.FOUNDRYIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8093";
            extra.FABRICIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8094";
        } else {
            extra.WORKIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8090/workiq";
            extra.WEBIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8090/webiq";
            extra.FOUNDRYIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8090/foundryiq";
            extra.FABRICIQ_EXPERT_ENDPOINT = "http://127.0.0.1:8090/fabriciq";
        }
        extra.WAYPOINT_API_BASE_URL = cfg.waypointApiBaseUrl || process.env.WAYPOINT_API_BASE_URL || "http://127.0.0.1:8000";
        extra.WAYPOINT_API_VERIFY_SSL = "false";
    }
    if (id === "webiq") extra.WEBIQ_SEARCH_CONTEXT_SIZE = cfg.webiqContext;
    return extra;
}

function serviceSpecs(forgeDir, cfg) {
    const specs = [];
    if (IQ_MODE === "stub") {
        specs.push({
            id: PROMPT_VALIDATOR_NODE,
            port: 8090,
            cmd: resolvePython(),
            args: [path.join("scripts", "run_local_prompt_agents.py"), "--port", "8090", "--mode", "stub"],
            cwd: forgeDir,
            env: augmentedEnv({ LOCAL_PROMPT_AGENTS_MODE: "stub" }),
            log: path.join(os.tmpdir(), "prompt-validators.log"),
        });
    }
    const azd = resolveBin("azd");
    const agents = IQ_MODE === "hosted" ? STACK_AGENTS : STACK_AGENTS.filter((agent) => agent.id === "pacioli");
    for (const a of agents) {
        specs.push({
            id: a.id,
            port: a.port,
            cmd: azd,
            args: ["ai", "agent", "run", a.name, "--no-inspector", "--port", String(a.port)],
            cwd: forgeDir,
            env: augmentedEnv(agentSpeedEnv(a.id, cfg)),
            log: path.join(os.tmpdir(), "agent-" + a.name + ".log"),
        });
    }
    return specs;
}

function spawnDetached(spec) {
    const fd = fs.openSync(spec.log, "a");
    try {
        let cmd = spec.cmd;
        let args = spec.args;
        let windowsVerbatimArguments = false;
        // Modern Node (>=18.20/20.12) refuses to spawn a .cmd/.bat directly and
        // throws EINVAL on Windows. `npm` resolves to `npm.cmd`, so wrap such
        // targets in cmd.exe. Each token is quoted (preserving spaces such as in
        // "Program Files") and the whole line wrapped per the cmd.exe `/s` rule;
        // windowsVerbatimArguments stops Node from re-quoting.
        if (process.platform === "win32" && /\.(cmd|bat)$/i.test(String(cmd))) {
            const line = [cmd, ...args]
                .map((a) => `"${String(a).replace(/"/g, '\\"')}"`)
                .join(" ");
            cmd = process.env.ComSpec || "cmd.exe";
            args = ["/d", "/s", "/c", `"${line}"`];
            windowsVerbatimArguments = true;
        }
        const child = spawn(cmd, args, {
            cwd: spec.cwd,
            env: spec.env,
            detached: true,
            windowsHide: true,
            windowsVerbatimArguments,
            stdio: ["ignore", fd, fd],
        });
        child.on("error", () => {
            /* failure is surfaced via the subsequent port probe */
        });
        child.unref();
    } finally {
        try {
            fs.closeSync(fd);
        } catch {
            /* ignore */
        }
    }
}

function waypointVenvPython(waypointDir) {
    return process.platform === "win32"
        ? path.join(waypointDir, "api", ".venv", "Scripts", "python.exe")
        : path.join(waypointDir, "api", ".venv", "bin", "python");
}

// Open a URL in the user's default browser. Cross-OS: `open` on macOS,
// `cmd /c start` on Windows, `xdg-open` on Linux. Only http/https URLs are
// allowed so this can't be coerced into launching arbitrary commands/files.
function openInDefaultBrowser(rawUrl) {
    let parsed;
    try {
        parsed = new URL(String(rawUrl));
    } catch {
        return { ok: false, error: `Invalid URL: ${rawUrl}` };
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return { ok: false, error: `Refusing to open non-http(s) URL: ${parsed.protocol}` };
    }
    const url = parsed.toString();
    let cmd;
    let args;
    if (process.platform === "win32") {
        cmd = process.env.ComSpec || "cmd.exe";
        // `start` treats a first quoted arg as the window title, so pass an
        // empty title before the URL.
        args = ["/d", "/s", "/c", "start", "", url];
    } else if (process.platform === "darwin") {
        cmd = "/usr/bin/open";
        args = [url];
    } else {
        cmd = resolveBin("xdg-open");
        args = [url];
    }
    try {
        const child = spawn(cmd, args, { env: augmentedEnv(), detached: true, stdio: "ignore", windowsHide: true });
        child.on("error", () => { /* surfaced via return below on sync failures only */ });
        child.unref();
        rememberCommand({ service: "waypoint", command: `open ${url}`, status: "completed" });
        return { ok: true, url };
    } catch (e) {
        return { ok: false, url, error: String(e?.message || e) };
    }
}

function waypointServiceSpecs(waypointDir) {
    return [
        {
            id: "waypoint-api",
            port: 8000,
            cmd: waypointVenvPython(waypointDir),
            args: ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload", "--reload-dir", "app"],
            cwd: path.join(waypointDir, "api"),
            env: augmentedEnv({
                APP_DATABASE_CONNECTION: WAYPOINT_DB_CONNECTION,
                APP_DEFAULT_SEED_ENABLED: "true",
                APP_LOCAL_AUTH_ENABLED: "true",
                APP_API_DOCS_ENABLED: "true",
            }),
            log: path.join(os.tmpdir(), "wpapi.log"),
        },
        {
            id: "waypoint-web",
            port: 5173,
            cmd: resolveBin("npm"),
            args: ["run", "dev", "--", "--port", "5173", "--host", "127.0.0.1"],
            cwd: path.join(waypointDir, "web"),
            env: augmentedEnv({ PORT: "5173" }),
            log: path.join(os.tmpdir(), "wpweb.log"),
        },
    ];
}

// Bring up the local Waypoint stack DIRECTLY on fixed ports (Postgres :5433 ->
// uvicorn API :8000 -> Vite web :5173), pointed at the user's persistent local
// database so their real data is visible. This intentionally does NOT delegate
// to Aspire, which provisions a fresh, separately-seeded Postgres container and
// assigns dynamic ports.
async function startWaypointLocal() {
    const waypointDir = resolveWaypointAppHostDir();
    const report = {
        waypointDir,
        started: [],
        alreadyUp: [],
        failed: [],
        status: {},
        waypointApiBaseUrl: WAYPOINT_API_BASE,
        waypointWebBaseUrl: WAYPOINT_WEB_BASE,
    };
    if (!waypointDir) {
        report.failed.push({ id: "waypoint-api", reason: "Waypoint repo not found; set WAYPOINT_DIR." });
        return report;
    }

    // 1) Postgres — start the persistent local container if it isn't listening.
    if (!(await checkPort(WAYPOINT_PG_PORT))) {
        const pg = await execCapture(
            resolveBin("docker"),
            ["start", WAYPOINT_PG_CONTAINER],
            { env: augmentedEnv(), windowsHide: true },
        );
        rememberCommand({
            service: "waypoint",
            command: `docker start ${WAYPOINT_PG_CONTAINER}`,
            status: pg.ok ? "started" : "failed",
            stdout: pg.stdout,
            stderr: pg.stderr,
            error: pg.error,
        });
        await pollReachable(async () => (await checkPort(WAYPOINT_PG_PORT)) ? "up" : "", { budgetMs: 20000, everyMs: 1500 });
    }
    report.status["waypoint-pg"] = await checkPort(WAYPOINT_PG_PORT);
    if (!report.status["waypoint-pg"]) {
        report.failed.push({
            id: "waypoint-pg",
            reason: `Postgres not reachable on :${WAYPOINT_PG_PORT}. Ensure the '${WAYPOINT_PG_CONTAINER}' container exists (docker start ${WAYPOINT_PG_CONTAINER}).`,
        });
    }

    // 2) API (uvicorn) + web (Vite), launched directly on fixed ports.
    for (const s of waypointServiceSpecs(waypointDir)) {
        if (await checkPort(s.port)) { report.alreadyUp.push(s.id); continue; }
        try {
            spawnDetached(s);
            rememberCommand({ service: "waypoint", command: `${s.id} on :${s.port}`, status: "started", log: s.log });
            report.started.push(s.id);
        } catch (e) {
            report.failed.push({ id: s.id, reason: String(e?.message || e) });
        }
    }

    const apiUp = await pollReachable(
        async () => (await waypointApiReachable(WAYPOINT_API_BASE)) ? WAYPOINT_API_BASE : "",
        { budgetMs: 45000, everyMs: 2000 },
    );
    const webUp = await pollReachable(
        async () => (await waypointWebReachable(WAYPOINT_WEB_BASE)) ? WAYPOINT_WEB_BASE : "",
        { budgetMs: 45000, everyMs: 2000 },
    );
    report.status["waypoint-api"] = Boolean(apiUp);
    report.status["waypoint-web"] = Boolean(webUp);
    if (report.started.includes("waypoint-api") && !apiUp) {
        const log = path.join(os.tmpdir(), "wpapi.log");
        report.failed.push({ id: "waypoint-api", reason: "API did not become reachable on :8000.", logTail: readLogTail(log) });
        rememberCommand({ service: "waypoint", command: "waypoint-api on :8000", status: "failed", stderr: "API not reachable on :8000", log });
    }
    if (report.started.includes("waypoint-web") && !webUp) {
        const log = path.join(os.tmpdir(), "wpweb.log");
        report.failed.push({ id: "waypoint-web", reason: "Web did not become reachable on :5173.", logTail: readLogTail(log) });
    }
    return report;
}

// Poll a probe that resolves to a truthy value (e.g. a reachable URL) until it
// succeeds or the time budget is exhausted. Returns the last truthy value, or
// "" if it never succeeded.
async function pollReachable(probe, { budgetMs = 60000, everyMs = 2500 } = {}) {
    const deadline = Date.now() + budgetMs;
    for (;;) {
        const result = await probe().catch(() => "");
        if (result) return result;
        if (Date.now() >= deadline) return "";
        await new Promise((r) => setTimeout(r, everyMs));
    }
}

function execCapture(cmd, args, opts = {}) {
    return new Promise((resolve) => {
        execFile(cmd, args, opts, (err, stdout, stderr) => {
            resolve({
                ok: !err,
                stdout: String(stdout || "").trim(),
                stderr: String(stderr || "").trim(),
                error: err ? String(err.message || err) : "",
            });
        });
    });
}

async function stopWaypointLocal() {
    const report = { stopped: [], notRunning: [], failed: [], status: {} };
    // Stop API + web by port (SIGTERM, then SIGKILL stragglers).
    for (const port of [8000, 5173]) {
        try {
            const pids = await killByPort(port, "SIGTERM");
            if (pids.length) {
                await new Promise((r) => setTimeout(r, 1200));
                const left = await pidsOnPort(port);
                if (left.length) await killByPort(port, "SIGKILL");
                report.stopped.push({ port, pids });
            } else {
                report.notRunning.push(port);
            }
        } catch (e) {
            report.failed.push({ id: `:${port}`, reason: String(e?.message || e) });
        }
    }
    // Stop the Postgres container (data persists in its named volume).
    const pg = await execCapture(
        resolveBin("docker"),
        ["stop", WAYPOINT_PG_CONTAINER],
        { env: augmentedEnv(), windowsHide: true },
    );
    rememberCommand({
        service: "waypoint",
        command: `docker stop ${WAYPOINT_PG_CONTAINER}`,
        status: pg.ok ? "completed" : "failed",
        stdout: pg.stdout,
        stderr: pg.stderr,
        error: pg.error,
    });
    await new Promise((r) => setTimeout(r, 1500));
    report.status["waypoint-api"] = await waypointApiReachable(WAYPOINT_API_BASE, 700);
    report.status["waypoint-web"] = await waypointWebReachable(WAYPOINT_WEB_BASE, 700);
    report.status["waypoint-pg"] = await checkPort(WAYPOINT_PG_PORT);
    report.waypointApiBaseUrl = WAYPOINT_API_BASE;
    report.waypointWebBaseUrl = WAYPOINT_WEB_BASE;
    return report;
}

async function firstReachableWaypointApiUrl(urls, timeoutMs = 700) {
    const seen = new Set();
    for (const candidate of urls.filter(Boolean)) {
        const normalized = candidate.replace(/\/+$/, "");
        if (seen.has(normalized)) continue;
        seen.add(normalized);
        if (await waypointApiReachable(normalized, timeoutMs)) return normalized;
    }
    return "";
}

function portFromUrl(value) {
    try {
        const url = new URL(value);
        if (url.port) return parseInt(url.port, 10);
        return url.protocol === "https:" ? 443 : 80;
    } catch {
        return null;
    }
}

async function processCommandLine(pid) {
    if (process.platform === "win32") {
        return execText(
            resolveBin("powershell"),
            [
                "-NoProfile",
                "-Command",
                `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`,
            ],
            { env: augmentedEnv(), windowsHide: true },
        );
    }
    return execText(resolveBin("ps"), ["-o", "command=", "-p", String(pid)], { env: augmentedEnv() });
}

async function killPid(pid) {
    if (process.platform === "win32") {
        await execCapture(
            resolveBin("powershell"),
            ["-NoProfile", "-Command", `Stop-Process -Id ${pid} -Force -ErrorAction SilentlyContinue`],
            { env: augmentedEnv(), windowsHide: true },
        );
        return;
    }
    try {
        process.kill(pid, "SIGTERM");
    } catch {
        /* already gone */
    }
}

async function stopWaypointApiProcesses(appHostDir, observedApiUrls = []) {
    const stopped = [];
    const ports = new Set();
    for (const value of observedApiUrls) {
        const port = portFromUrl(value);
        if (Number.isInteger(port) && port > 0) ports.add(port);
    }
    // Local non-Aspire Waypoint API runs commonly bind :8000; only stop it when
    // the owning process command line is under the Waypoint repo.
    ports.add(8000);
    const waypointRoot = path.resolve(appHostDir).toLowerCase();
    for (const port of ports) {
        const pids = await pidsOnPort(port);
        for (const pid of pids) {
            const commandLine = await processCommandLine(pid);
            if (!commandLine || !commandLine.toLowerCase().includes(waypointRoot)) continue;
            await killPid(pid);
            stopped.push({ id: "waypoint-api", port, pid });
        }
    }
    return stopped;
}

function readLogTail(file, maxChars = 2500) {
    try {
        if (!file || !fs.existsSync(file)) return "";
        const text = fs.readFileSync(file, "utf8");
        return text.length > maxChars ? text.slice(text.length - maxChars) : text;
    } catch {
        return "";
    }
}

function diagnosticLogSources() {
    const tmp = os.tmpdir();
    return [
        { id: "waypoint", label: "Waypoint API", file: path.join(tmp, "wpapi.log") },
        { id: "waypoint-web", label: "Waypoint Web", file: path.join(tmp, "wpweb.log") },
        { id: "pacioli", label: "pacioli", file: path.join(tmp, "agent-pacioli.log") },
        { id: "prompt-validators", label: "IQ-tool agent stubs", file: path.join(tmp, "prompt-validators.log") },
        ...STACK_AGENTS.filter((agent) => agent.id !== "pacioli").map((agent) => ({
            id: agent.id,
            label: agent.name,
            file: path.join(tmp, "agent-" + agent.name + ".log"),
        })),
    ];
}

function buildDiagnostics() {
    return {
        ts: Date.now(),
        commands: commandHistory,
        canvas: {
            workspacePath: WORKSPACE_PATH,
            iqMode: IQ_MODE,
            servers: [...servers.entries()].map(([instanceId, entry]) => ({
                instanceId,
                url: entry.url,
                createdAt: entry.createdAt,
                openedAt: entry.openedAt,
                openCount: entry.openCount || 0,
                requestCount: entry.requestCount || 0,
                lastRequestAt: entry.lastRequestAt || null,
                lastPath: entry.lastPath || null,
                pathCounts: entry.pathCounts || {},
            })),
            events: canvasEvents.slice(0, 40),
        },
        logs: diagnosticLogSources().map((source) => {
            const tail = readLogTail(source.file, 12000);
            return {
                ...source,
                exists: Boolean(tail),
                tail,
            };
        }),
    };
}

function truncateDiagnosticLogs() {
    const cleared = [];
    for (const source of diagnosticLogSources()) {
        try {
            fs.writeFileSync(source.file, "", "utf8");
            cleared.push(source.id);
        } catch {
            /* log file may not exist yet or may be locked briefly. */
        }
    }
    return cleared;
}

async function resetLocalState() {
    const report = {
        stoppedForge: null,
        stoppedWaypoint: null,
        clearedLogs: [],
        clearedCommandHistory: 0,
        clearedRunJobs: 0,
        state: null,
    };
    report.stoppedForge = await stopStack();
    report.stoppedWaypoint = await stopWaypointLocal();
    report.clearedRunJobs = runJobs.size;
    runJobs.clear();
    report.clearedCommandHistory = commandHistory.length;
    commandHistory.length = 0;
    report.clearedLogs = truncateDiagnosticLogs();
    report.state = await buildState();
    return report;
}

function buildCanvasDebug() {
    return buildDiagnostics().canvas;
}

async function serviceHealthy(spec, cfg) {
    if (spec.id === PROMPT_VALIDATOR_NODE) {
        const health = await fetchJson("http://127.0.0.1:8090/healthz", {}, 1200).catch(() => null);
        const agents = new Set(Array.isArray(health?.body?.agents) ? health.body.agents : []);
        return Boolean(health?.ok && PROMPT_AGENT_NODES.every((node) => agents.has(node.agentName)));
    }
    return checkPort(spec.port);
}

async function waitForServices(specs, timeoutMs, cfg) {
    const deadline = Date.now() + timeoutMs;
    const pending = new Set(specs.map((s) => s.id));
    while (pending.size && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        for (const spec of specs) {
            if (pending.has(spec.id) && await serviceHealthy(spec, cfg)) pending.delete(spec.id);
        }
    }
    return [...pending];
}

async function startStack() {
    const forgeDir = WORKSPACE_PATH || process.cwd();
    const report = {
        forgeDir,
        waypointManagedBy: "Direct local launch (Postgres :5433, API :8000, web :5173)",
        started: [],
        alreadyUp: [],
        failed: [],
    };

    const cfg = await resolveLaunchConfig(forgeDir);
    const specs = serviceSpecs(forgeDir, cfg);

    // Preflight: the hosted agents with IQ tools require Foundry endpoint + model. Without
    // those they crash on KeyError before binding, so fail fast with a clear reason.
    const agentsBlocked = !cfg.endpoint || !cfg.model;
    report.foundryEndpointSet = Boolean(cfg.endpoint);
    report.modelDeployment = cfg.model || null;
    report.localIqMode = IQ_MODE;
    report.waypointApiBaseUrl = cfg.waypointApiBaseUrl;

    for (const s of specs) {
        if (await checkPort(s.port)) {
            if (await serviceHealthy(s, cfg)) {
                report.alreadyUp.push(s.id);
                continue;
            }
            report.failed.push({
                id: s.id,
                reason: `Port ${s.port} is already in use, but ${s.id} did not pass its health check.`,
            });
            continue;
        }
        if (agentsBlocked && AGENT_IDS.has(s.id)) {
            report.failed.push({
                id: s.id,
                reason:
                    "FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are required — " +
                    "export them or select an azd env before spin-up.",
            });
            continue;
        }
        try {
            spawnDetached(s);
            rememberCommand({
                service: s.id,
                command: [s.cmd, ...s.args].join(" "),
                status: "started",
                log: s.log,
            });
            report.started.push(s.id);
        } catch (e) {
            report.failed.push({ id: s.id, reason: String(e?.message || e) });
        }
    }

    // Wait for the services we just launched (azd agents take ~45–60s each).
    const waitSpecs = specs.filter((s) => report.started.includes(s.id));
    const pending = await waitForServices(waitSpecs, 150000, cfg);

    report.status = {};
    for (const s of specs) report.status[s.id] = await serviceHealthy(s, cfg);
    for (const id of pending) {
        const spec = specs.find((s) => s.id === id);
        if (!spec || report.status[id] || report.failed.some((f) => f.id === id)) continue;
        report.failed.push({
            id,
            reason: `${id} did not become healthy on port ${spec.port}.`,
            logTail: readLogTail(spec.log),
        });
    }
    report.observed = {
        "waypoint-api": await waypointApiReachable(cfg.waypointApiBaseUrl),
    };
    report.upCount = Object.values(report.status).filter(Boolean).length;
    report.total = Object.keys(report.status).length;
    return report;
}

// ---------------------------------------------------------------------------
// Spin Forge-local services DOWN. Waypoint is Aspire-managed and is not stopped
// here.
// ---------------------------------------------------------------------------
const STACK_PORTS = [
    ...(IQ_MODE === "stub"
        ? [{ id: PROMPT_VALIDATOR_NODE, port: 8090 }]
        : [
              { id: "workiq", port: 8091 },
              { id: "webiq", port: 8092 },
              { id: "foundryiq", port: 8093 },
              { id: "fabriciq", port: 8094 },
          ]),
    { id: "pacioli", port: 8088 },
];

function pidsOnPort(port) {
    return new Promise((resolve) => {
        if (process.platform === "win32") {
            execFile(
                resolveBin("powershell"),
                [
                    "-NoProfile",
                    "-Command",
                    `(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique`,
                ],
                { env: augmentedEnv() },
                (err, stdout) => {
                    if (err || !stdout) return resolve([]);
                    const pids = String(stdout)
                        .split(/\s+/)
                        .map((s) => parseInt(s, 10))
                        .filter((n) => Number.isInteger(n) && n > 1);
                    resolve([...new Set(pids)]);
                },
            );
            return;
        }
        execFile(
            resolveBin("lsof"),
            ["-ti", `tcp:${port}`, "-sTCP:LISTEN"],
            { env: augmentedEnv() },
            (err, stdout) => {
                if (err || !stdout) return resolve([]);
                const pids = String(stdout)
                    .split(/\s+/)
                    .map((s) => parseInt(s, 10))
                    .filter((n) => Number.isInteger(n) && n > 1);
                resolve([...new Set(pids)]);
            },
        );
    });
}

function pgidOf(pid) {
    return new Promise((resolve) => {
        execFile(resolveBin("ps"), ["-o", "pgid=", "-p", String(pid)], (err, stdout) => {
            if (err) return resolve(null);
            const v = parseInt(String(stdout).trim(), 10);
            resolve(Number.isInteger(v) ? v : null);
        });
    });
}

async function killByPort(port, signal) {
    const pids = await pidsOnPort(port);
    if (process.platform === "win32") {
        if (pids.length) {
            await new Promise((resolve) => {
                execFile(
                    resolveBin("powershell"),
                    ["-NoProfile", "-Command", `Stop-Process -Id ${pids.join(",")} -Force -ErrorAction SilentlyContinue`],
                    { env: augmentedEnv() },
                    () => resolve(),
                );
            });
        }
        return pids;
    }
    const groups = new Set();
    for (const pid of pids) {
        const pgid = await pgidOf(pid);
        if (pgid && pgid > 1) groups.add(pgid);
    }
    for (const g of groups) {
        try {
            process.kill(-g, signal);
        } catch {
            /* group already gone */
        }
    }
    for (const pid of pids) {
        try {
            process.kill(pid, signal);
        } catch {
            /* already gone */
        }
    }
    return pids;
}

async function stopStack() {
    const report = { stopped: [], notRunning: [], failed: [] };

    for (const s of STACK_PORTS) {
        try {
            const pids = await killByPort(s.port, "SIGTERM");
            if (pids.length) report.stopped.push({ id: s.id, port: s.port, pids });
            else report.notRunning.push(s.id);
        } catch (e) {
            report.failed.push({ id: s.id, reason: String(e?.message || e) });
        }
    }

    // Give services a moment to exit cleanly, then SIGKILL anything still bound.
    await new Promise((r) => setTimeout(r, 2500));
    for (const s of STACK_PORTS) {
        try {
            if (await checkPort(s.port)) await killByPort(s.port, "SIGKILL");
        } catch {
            /* ignore */
        }
    }
    await new Promise((r) => setTimeout(r, 500));

    report.status = {};
    for (const s of STACK_PORTS) report.status[s.id] = await checkPort(s.port);
    report.upCount = Object.values(report.status).filter(Boolean).length;
    report.total = Object.keys(report.status).length;
    report.stillUp = STACK_PORTS.filter((s) => report.status[s.id]).map((s) => s.id);
    rememberCommand({
        service: "forge-stack",
        command: "stop local Forge stack",
        status: report.failed.length || report.stillUp.length ? "warning" : "completed",
        stdout: `${report.stopped.length} stopped; ${report.notRunning.length} not running`,
        stderr: report.failed.map((f) => `${f.id}: ${f.reason}`).join("\n"),
    });
    return report;
}

// ---------------------------------------------------------------------------
// Per-node control: start / stop / restart one service or agent individually.
// Builds a controller map keyed by node id, reusing the exact same launch
// specs and stop primitives as the bulk start/stop flows so behaviour stays
// consistent (and cross-OS: docker/spawnDetached/killByPort already branch on
// win32). Each controller exposes start(), stop(), and a healthy() probe.
// ---------------------------------------------------------------------------
async function stopByPortGraceful(port) {
    const pids = await killByPort(port, "SIGTERM");
    if (pids.length) {
        await new Promise((r) => setTimeout(r, 1200));
        if ((await pidsOnPort(port)).length) await killByPort(port, "SIGKILL");
    }
    return pids;
}

async function buildNodeControllers() {
    const map = new Map();

    // Postgres — the persistent local container (data survives stop/start).
    map.set("waypoint-pg", {
        port: WAYPOINT_PG_PORT,
        kind: "container",
        healthy: () => checkPort(WAYPOINT_PG_PORT),
        start: async () => {
            if (await checkPort(WAYPOINT_PG_PORT)) return;
            const pg = await execCapture(
                resolveBin("docker"),
                ["start", WAYPOINT_PG_CONTAINER],
                { env: augmentedEnv(), windowsHide: true },
            );
            rememberCommand({
                service: "waypoint",
                command: `docker start ${WAYPOINT_PG_CONTAINER}`,
                status: pg.ok ? "started" : "failed",
                stdout: pg.stdout,
                stderr: pg.stderr,
                error: pg.error,
            });
            if (!pg.ok && !(await checkPort(WAYPOINT_PG_PORT))) {
                throw new Error(
                    `docker start ${WAYPOINT_PG_CONTAINER} failed: ${pg.error || pg.stderr || "unknown error"}`,
                );
            }
            await pollReachable(async () => ((await checkPort(WAYPOINT_PG_PORT)) ? "up" : ""), { budgetMs: 20000, everyMs: 1500 });
        },
        stop: async () => {
            const pg = await execCapture(
                resolveBin("docker"),
                ["stop", WAYPOINT_PG_CONTAINER],
                { env: augmentedEnv(), windowsHide: true },
            );
            rememberCommand({
                service: "waypoint",
                command: `docker stop ${WAYPOINT_PG_CONTAINER}`,
                status: pg.ok ? "completed" : "failed",
                stdout: pg.stdout,
                stderr: pg.stderr,
                error: pg.error,
            });
            await new Promise((r) => setTimeout(r, 1200));
        },
    });

    // Waypoint API + web — direct spawnDetached on fixed ports.
    const waypointDir = resolveWaypointAppHostDir();
    const waypointReach = {
        "waypoint-api": () => waypointApiReachable(WAYPOINT_API_BASE, 700),
        "waypoint-web": () => waypointWebReachable(WAYPOINT_WEB_BASE, 700),
    };
    const waypointPorts = { "waypoint-api": 8000, "waypoint-web": 5173 };
    const waypointSpecs = waypointDir ? waypointServiceSpecs(waypointDir) : [];
    for (const id of ["waypoint-api", "waypoint-web"]) {
        const spec = waypointSpecs.find((s) => s.id === id);
        const reach = waypointReach[id];
        map.set(id, {
            port: waypointPorts[id],
            kind: "process",
            healthy: reach,
            start: async () => {
                if (await reach()) return;
                if (!spec) {
                    throw new Error("Waypoint repo not found; set WAYPOINT_DIR to start " + id + ".");
                }
                // Port occupied but not reachable (stale process holding the
                // port): clear it first so the fresh spawn can bind.
                if (await checkPort(waypointPorts[id])) {
                    await stopByPortGraceful(waypointPorts[id]);
                    await new Promise((r) => setTimeout(r, 600));
                }
                spawnDetached(spec);
                rememberCommand({ service: "waypoint", command: `${spec.id} on :${spec.port}`, status: "started", log: spec.log });
                const up = await pollReachable(async () => ((await reach()) ? "up" : ""), { budgetMs: 45000, everyMs: 2000 });
                if (!up) throw new Error(`${id} did not become reachable on :${waypointPorts[id]}.`);
            },
            stop: async () => stopByPortGraceful(waypointPorts[id]),
        });
    }

    // Forge-local stack services (IQ stub host / hosted IQ agents / orchestrator).
    const forgeDir = WORKSPACE_PATH || process.cwd();
    const cfg = await resolveLaunchConfig(forgeDir);
    for (const spec of serviceSpecs(forgeDir, cfg)) {
        map.set(spec.id, {
            port: spec.port,
            kind: "process",
            healthy: () => serviceHealthy(spec, cfg),
            start: async () => {
                const portUp = await checkPort(spec.port);
                if (portUp && (await serviceHealthy(spec, cfg))) return;
                if ((!cfg.endpoint || !cfg.model) && AGENT_IDS.has(spec.id)) {
                    throw new Error(
                        "FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are required to start " + spec.id + ".",
                    );
                }
                // Port occupied but not healthy (e.g. a stale process from older
                // code holding the port): a fresh spawn can't bind, so clear it
                // first instead of spawning a doomed duplicate.
                if (portUp) {
                    await stopByPortGraceful(spec.port);
                    await new Promise((r) => setTimeout(r, 600));
                }
                spawnDetached(spec);
                rememberCommand({ service: spec.id, command: [spec.cmd, ...spec.args].join(" "), status: "started", log: spec.log });
                const budget = spec.id === "pacioli" ? 90000 : 150000;
                const pending = await waitForServices([spec], budget, cfg);
                if (pending.includes(spec.id) && !(await serviceHealthy(spec, cfg))) {
                    throw new Error(`${spec.id} did not become healthy on :${spec.port}.`);
                }
            },
            stop: async () => stopByPortGraceful(spec.port),
        });
    }

    // In stub mode the 4 expert nodes (workiq/webiq/foundryiq/fabriciq) are all
    // served by the single shared stub host (:8090). They have no individual
    // process, so alias each to the stub-host controller: starting/stopping one
    // brings the shared host (and thus all experts) up or down together.
    const stubHost = map.get(PROMPT_VALIDATOR_NODE);
    if (stubHost) {
        for (const node of PROMPT_AGENT_NODES) {
            if (!map.has(node.id)) {
                map.set(node.id, { ...stubHost, shared: PROMPT_VALIDATOR_NODE });
            }
        }
    }

    return map;
}

async function controlNode(id, action) {
    const valid = new Set(["start", "stop", "restart"]);
    const report = { id, action, port: null };
    if (!valid.has(action)) {
        report.error = `Unknown action '${action}'. Use start, stop, or restart.`;
        report.ok = false;
        return report;
    }
    let controllers;
    try {
        controllers = await buildNodeControllers();
    } catch (e) {
        report.error = "Failed to resolve node controllers: " + String(e?.message || e);
        report.ok = false;
        return report;
    }
    const ctrl = controllers.get(id);
    if (!ctrl) {
        report.error = `Unknown node '${id}'.`;
        report.ok = false;
        return report;
    }
    report.port = ctrl.port ?? null;
    if (ctrl.shared) report.shared = ctrl.shared;
    try {
        if (action === "stop" || action === "restart") {
            await ctrl.stop();
        }
        if (action === "restart") {
            await new Promise((r) => setTimeout(r, 800));
        }
        if (action === "start" || action === "restart") {
            await ctrl.start();
        }
    } catch (e) {
        report.error = String(e?.message || e);
    }
    report.up = Boolean(await ctrl.healthy().catch(() => false));
    report.ok = action === "stop" ? !report.up && !report.error : report.up && !report.error;
    return report;
}
// ---------------------------------------------------------------------------
function sendJson(res, status, payload) {
    const body = JSON.stringify(payload);
    res.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    });
    res.end(body);
}

async function readBody(req) {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const raw = Buffer.concat(chunks).toString("utf8");
    return raw ? JSON.parse(raw) : {};
}

async function startServer(instanceId) {
    const entry = {
        server: null,
        url: "",
        instanceId,
        createdAt: Date.now(),
        openedAt: Date.now(),
        openCount: 0,
        requestCount: 0,
        lastRequestAt: null,
        lastPath: null,
        pathCounts: {},
    };
    const server = createServer(async (req, res) => {
        entry.requestCount += 1;
        entry.lastRequestAt = Date.now();
        const url = new URL(req.url || "/", "http://127.0.0.1");
        entry.lastPath = url.pathname;
        entry.pathCounts[url.pathname] = (entry.pathCounts[url.pathname] || 0) + 1;
        try {
            if (req.method === "GET" && url.pathname === "/") {
                res.writeHead(200, {
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-store",
                });
                res.end(PAGE_HTML);
                return;
            }
            if (req.method === "GET" && url.pathname === "/favicon.ico") {
                res.writeHead(204, { "Cache-Control": "no-store" });
                res.end();
                return;
            }
            if (req.method === "GET" && url.pathname === "/state") {
                sendJson(res, 200, await buildState());
                return;
            }
            if (req.method === "GET" && url.pathname === "/diagnostics") {
                sendJson(res, 200, buildDiagnostics());
                return;
            }
            if (req.method === "GET" && url.pathname === "/healthz") {
                sendJson(res, 200, {
                    ok: true,
                    instanceId,
                    createdAt: entry.createdAt,
                    requestCount: entry.requestCount,
                    lastRequestAt: entry.lastRequestAt,
                    lastPath: entry.lastPath,
                });
                return;
            }
            if (req.method === "GET" && url.pathname === "/client-pixel") {
                rememberCanvasEvent({
                    source: "iframe-pixel",
                    instanceId,
                    kind: url.searchParams.get("kind") || "pixel",
                    userAgent: req.headers["user-agent"] || "",
                });
                res.writeHead(204, { "Cache-Control": "no-store" });
                res.end();
                return;
            }
            if (req.method === "POST" && url.pathname === "/client-log") {
                const body = await readBody(req).catch((err) => ({ kind: "parse_error", message: String(err?.message || err) }));
                rememberCanvasEvent({
                    source: "iframe",
                    instanceId,
                    userAgent: req.headers["user-agent"] || "",
                    ...body,
                });
                sendJson(res, 200, { ok: true });
                return;
            }
            if (req.method === "POST" && url.pathname === "/reset-local-state") {
                try {
                    sendJson(res, 200, await resetLocalState());
                } catch (err) {
                    sendJson(res, 200, { error: String(err?.message || err) });
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/run") {
                const body = await readBody(req);
                try {
                    const job = startRunJob(body.target || "pacioli", body.message || DEFAULT_PROMPT, body);
                    sendJson(res, 202, job);
                } catch (err) {
                    sendJson(res, 200, {
                        error: err instanceof CanvasError ? err.message : String(err?.message || err),
                    });
                }
                return;
            }
            if (req.method === "GET" && url.pathname === "/run-status") {
                const id = url.searchParams.get("id") || "";
                const job = runJobs.get(id);
                if (!job) {
                    sendJson(res, 404, { error: "run job not found" });
                } else {
                    sendJson(res, 200, publicRunJob(job));
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/start-stack") {
                try {
                    sendJson(res, 200, await startStack());
                } catch (err) {
                    sendJson(res, 200, { error: String(err?.message || err) });
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/start-waypoint") {
                try {
                    sendJson(res, 200, await startWaypointLocal());
                } catch (err) {
                    sendJson(res, 200, { error: String(err?.message || err) });
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/stop-waypoint") {
                try {
                    sendJson(res, 200, await stopWaypointLocal());
                } catch (err) {
                    sendJson(res, 200, { error: String(err?.message || err) });
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/stop-stack") {
                try {
                    sendJson(res, 200, await stopStack());
                } catch (err) {
                    sendJson(res, 200, { error: String(err?.message || err) });
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/control-node") {
                const body = await readBody(req);
                try {
                    sendJson(res, 200, await controlNode(String(body.id || ""), String(body.action || "")));
                } catch (err) {
                    sendJson(res, 200, { error: String(err?.message || err) });
                }
                return;
            }
            if (req.method === "POST" && url.pathname === "/open-external") {
                const body = await readBody(req);
                const target = body.id === "waypoint-web" ? WAYPOINT_WEB_BASE
                    : body.id === "waypoint-api" ? WAYPOINT_API_BASE
                    : body.url || "";
                sendJson(res, 200, openInDefaultBrowser(target));
                return;
            }
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("not found");
        } catch (err) {
            sendJson(res, 500, { error: String(err?.message || err) });
        }
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const addr = server.address();
    const port = typeof addr === "object" && addr ? addr.port : 0;
    entry.server = server;
    entry.url = `http://127.0.0.1:${port}/`;
    rememberCanvasEvent({ source: "provider", instanceId, kind: "server_started", url: entry.url });
    return entry;
}

// ---------------------------------------------------------------------------
// Canvas declaration
// ---------------------------------------------------------------------------
const session = await joinSession({
    canvases: [
        createCanvas({
            id: "pipeline-control",
            displayName: "Pipeline Mission Control",
            description:
                "Live status board + fan-out visualizer for the local invoice-assurance pipeline: " +
                "Waypoint API, local agents with IQ tools, and the Pacioli deterministic workflow. " +
                "Shows which ports are up and lets you trigger a run and inspect the write-plan preview.",
            actions: [
                {
                    name: "refresh_state",
                    description: "Return the current node/port status and Waypoint case+audit summary.",
                    handler: async () => buildState(),
                },
                {
                    name: "run_pipeline",
                    description:
                        "Trigger an assurance run against the Pacioli /responses endpoint and return the parsed workflow result.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            target: { type: "string", enum: ["pacioli"] },
                            message: { type: "string" },
                            invoiceId: { type: "string" },
                            pdfUri: { type: "string" },
                            pdfBase64: { type: "string" },
                            batchId: { type: "string" },
                        },
                    },
                    handler: async (ctx) =>
                        runPipeline(
                            ctx.input?.target || "pacioli",
                            ctx.input?.message || DEFAULT_PROMPT,
                            ctx.input || {},
                        ),
                },
                {
                    name: "start_stack",
                    description:
                        "Spin up the Forge-local pipeline processes as detached processes that survive the session: " +
                        "fast IQ-tool agent stub host (:8090) by default, or hosted agents with IQ tools (:8091-:8094) when PIPELINE_IQ_MODE=hosted, plus Pacioli (:8088). " +
                        "Waypoint is launched separately via the Start Waypoint toggle; Pacioli talks to its API boundary only. " +
                        "Returns the per-service up/down status.",
                    handler: async () => startStack(),
                },
                {
                    name: "start_waypoint",
                    description:
                        "Start the local Waypoint stack directly on fixed ports: the persistent Postgres " +
                        "container (:5433), the uvicorn API (:8000), and the Vite web frontend (:5173). " +
                        "Points the API at the user's persistent local database so real data is visible. " +
                        "Returns per-service up/down status.",
                    handler: async () => startWaypointLocal(),
                },
                {
                    name: "stop_waypoint",
                    description:
                        "Stop the local Waypoint API (:8000) and web (:5173) by port and stop the Postgres " +
                        "container (data persists in its volume). Forge-local IQ/Pacioli processes are left " +
                        "alone. Returns per-service up/down status.",
                    handler: async () => stopWaypointLocal(),
                },
                {
                    name: "stop_stack",
                    description:
                        "Spin down Forge-local pipeline processes: terminate whatever is listening on " +
                        "the local IQ ports and Pacioli workflow (:8088). Each service is sent " +
                        "SIGTERM, then SIGKILL for stragglers. Waypoint is managed separately and left running. " +
                        "Returns the per-service up/down status.",
                    handler: async () => stopStack(),
                },
                {
                    name: "control_node",
                    description:
                        "Start, stop, or restart a single pipeline node by id (e.g. waypoint-pg, waypoint-api, " +
                        "waypoint-web, prompt-validators, pacioli, or a hosted IQ agent). Reuses the same launch " +
                        "specs and stop primitives as the bulk flows. Returns the node's resulting up/down status.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            id: { type: "string" },
                            action: { type: "string", enum: ["start", "stop", "restart"] },
                        },
                        required: ["id", "action"],
                    },
                    handler: async (ctx) => controlNode(ctx.input?.id || "", ctx.input?.action || ""),
                },
                {
                    name: "open_external",
                    description:
                        "Open a pipeline node's web URL in the system default browser. Pass id 'waypoint-web' " +
                        "(:5173) or 'waypoint-api' (:8000), or an explicit http(s) url. Cross-OS (open/start/xdg-open).",
                    inputSchema: {
                        type: "object",
                        properties: {
                            id: { type: "string" },
                            url: { type: "string" },
                        },
                    },
                    handler: async (ctx) => {
                        const id = ctx.input?.id || "";
                        const target = id === "waypoint-web" ? WAYPOINT_WEB_BASE
                            : id === "waypoint-api" ? WAYPOINT_API_BASE
                            : ctx.input?.url || "";
                        return openInDefaultBrowser(target);
                    },
                },
                {
                    name: "reset_local_state",
                    description:
                        "Stop Forge-local services and the local Waypoint API, clear command history, run jobs, and known diagnostic logs, then return fresh state.",
                    handler: async () => resetLocalState(),
                },
                {
                    name: "debug_canvas",
                    description:
                        "Return provider-side canvas server/open/client-load diagnostics for investigating blank or stale UI panels.",
                    handler: async () => buildCanvasDebug(),
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(ctx.instanceId);
                    servers.set(ctx.instanceId, entry);
                }
                entry.openedAt = Date.now();
                entry.openCount = (entry.openCount || 0) + 1;
                const openUrl = `${entry.url}?open=${entry.openCount}&ts=${entry.openedAt}`;
                rememberCanvasEvent({
                    source: "provider",
                    instanceId: ctx.instanceId,
                    kind: "open",
                    url: openUrl,
                    input: ctx.input || null,
                    openCount: entry.openCount,
                });
                return { title: "Pipeline Mission Control", url: openUrl, status: `ready · open ${entry.openCount}` };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    servers.delete(ctx.instanceId);
                    rememberCanvasEvent({ source: "provider", instanceId: ctx.instanceId, kind: "close", url: entry.url });
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});

void session;

// ---------------------------------------------------------------------------
// Iframe document (served at "/"). Vanilla JS; polls /state, POSTs /run.
// ---------------------------------------------------------------------------
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

const PAGE_HTML = `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Pipeline Mission Control</title>
<style>
  :root {
    color-scheme: light dark;
    --pc-bg: #ffffff;
    --pc-fg: #1f2328;
    --pc-muted: #59636e;
    --pc-border: #d1d9e0;
    --pc-surface: #f6f8fa;
    --pc-green: #1a7f37;
    --pc-red: #cf222e;
    --pc-blue: #0969da;
    --pc-accent: #0969da;
    --pc-green-tint: rgba(26,127,55,0.10);
    --pc-red-tint: rgba(207,34,46,0.10);
    --pc-blue-tint: rgba(9,105,218,0.12);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --pc-bg: #0d1117;
      --pc-fg: #e6edf3;
      --pc-muted: #9198a1;
      --pc-border: #30363d;
      --pc-surface: #161b22;
      --pc-green: #3fb950;
      --pc-red: #f85149;
      --pc-blue: #58a6ff;
      --pc-accent: #58a6ff;
      --pc-green-tint: rgba(63,185,80,0.15);
      --pc-red-tint: rgba(248,81,73,0.15);
      --pc-blue-tint: rgba(88,166,255,0.15);
    }
  }
  * { box-sizing: border-box; }
  html { background: var(--pc-bg); color: var(--pc-fg); min-height: 100%; }
  body {
    margin: 0;
    background: var(--pc-bg);
    color: var(--pc-fg);
    font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
    font-size: var(--text-body-medium, 13px);
    line-height: var(--leading-body-medium, 20px);
  }
  .wrap { padding: 14px 16px 40px; max-width: 1100px; margin: 0 auto; }
  h1 { font-size: var(--text-title-large, 20px); font-weight: var(--font-weight-semibold, 600); margin: 0 0 2px; }
  .sub { color: var(--pc-muted); font-size: 12px; margin-bottom: 14px; }
  .muted { color: var(--pc-muted); }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  .card {
    border: 1px solid var(--pc-border);
    border-radius: 8px; padding: 10px 12px; background: var(--pc-surface);
  }
  section { margin-top: 18px; }
  .section-title { font-weight: 600; font-size: 13px; margin: 0 0 8px; text-transform: uppercase; letter-spacing: .04em; color: var(--pc-muted); }
  details.services {
    border: 1px solid var(--pc-border);
    border-radius: 8px;
    background: var(--pc-surface);
    padding: 0;
    margin-top: 10px;
    overflow: hidden;
  }
  details.services summary {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 34px;
    padding: 7px 10px;
  }
  details.services[open] summary { border-bottom: 1px solid var(--pc-border); }
  details.services summary .section-title { margin: 0; }
  details.diagnostics {
    border: 1px solid var(--pc-border);
    border-radius: 8px;
    background: var(--pc-surface);
    padding: 0;
    margin-top: 18px;
    overflow: hidden;
  }
  details.diagnostics summary {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 34px;
    padding: 7px 10px;
  }
  details.diagnostics[open] summary { border-bottom: 1px solid var(--pc-border); }
  details.diagnostics summary .section-title { margin: 0; }
  .diagnostics-body { padding: 12px; display: grid; gap: 10px; }
  .diag-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .diag-log {
    margin: 0;
    max-height: 260px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    border: 1px solid var(--pc-border);
    border-radius: 8px;
    padding: 10px;
    background: var(--pc-bg);
    font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace);
    font-size: 11px;
    line-height: 16px;
  }
  .command-list { display: grid; gap: 6px; }
  .command-row {
    border: 1px solid var(--pc-border);
    border-radius: 8px;
    padding: 7px 9px;
    background: var(--pc-bg);
  }
  .command-row .cmd { font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace); font-size: 11px; }
  .diag-output { margin-top: 5px; color: var(--pc-muted); white-space: pre-wrap; font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace); font-size: 11px; }
  #serviceSummary { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; flex: 1; }
  .summary-hint { color: var(--pc-muted); font-size: 11px; white-space: nowrap; }
  .status-chip {
    border: 1px solid var(--pc-border);
    border-radius: 999px;
    padding: 1px 7px;
    font-size: 11px;
    color: var(--pc-muted);
  }
  .status-chip.ok { border-color: var(--pc-green); color: var(--pc-green); }
  .status-chip.bad { border-color: var(--pc-red); color: var(--pc-red); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap: 8px; }
  details.services .grid {
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
    padding: 12px;
  }
  .node {
    border: 1px solid var(--pc-border);
    border-left-width: 4px; border-radius: 6px; padding: 8px 10px; background: var(--pc-surface);
  }
  .service-node {
    min-height: 88px;
    border-radius: 10px;
    padding: 12px 14px;
    background: var(--pc-bg);
  }
  .node .name { font-weight: 600; display: flex; justify-content: space-between; align-items: center; gap: 6px; }
  .node .meta { font-size: 11px; color: var(--pc-muted); margin-top: 2px; }
  .service-node .meta { margin-top: 7px; line-height: 16px; }
  .service-node .endpoint { font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace); word-break: break-all; }
  .service-node .state { font-weight: 600; color: var(--pc-fg); }
  .service-node .controls { display: flex; gap: 6px; margin-top: 9px; flex-wrap: wrap; }
  .node-btn {
    font: inherit; font-size: 11px; font-weight: 600; line-height: 1;
    cursor: pointer; border-radius: 5px; padding: 4px 8px;
    border: 1px solid var(--pc-border); background: transparent; color: var(--pc-fg);
  }
  .node-btn:hover:not(:disabled) { background: var(--pc-surface); border-color: var(--pc-accent); }
  .node-btn.busy, .node-btn:disabled { opacity: .5; cursor: default; }
  .node-btn .spinner { width: 10px; height: 10px; border-width: 2px; vertical-align: -1px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
  .up   { border-left-color: var(--pc-green); }
  .down { border-left-color: var(--pc-red); opacity: .72; }
  .up .dot   { background: var(--pc-green); box-shadow: 0 0 6px var(--pc-green); }
  .down .dot { background: var(--pc-red); }
  .touched { outline: 2px solid var(--pc-blue); outline-offset: 1px; }
  .flow { display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; }
  .flow .col { display: flex; flex-direction: column; gap: 6px; justify-content: center; }
  .flow .arrow { align-self: center; color: var(--pc-muted); font-size: 18px; padding: 0 2px; }
  .pacioli-frame {
    border: 1px solid var(--pc-border);
    border-left: 4px solid var(--pc-blue);
    border-radius: 10px;
    padding: 10px;
    background: var(--pc-surface);
    width: 100%;
  }
  .pacioli-frame.frame-down { border-left-color: var(--pc-red); }
  .pacioli-frame.frame-touched { box-shadow: 0 0 0 2px var(--pc-blue-tint); }
  .pacioli-frame .frame-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    padding-bottom: 8px;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--pc-border);
  }
  .pacioli-frame .frame-body { display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; }
  .pacioli-frame .frame-meta { color: var(--pc-muted); font-size: 11px; font-weight: 400; }
  .pacioli-frame .boundary-note { color: var(--pc-muted); font-size: 11px; margin-top: 8px; }
  .workflow-lane { display: flex; flex-direction: column; gap: 10px; width: 100%; }
  .workflow-chain { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .workflow-arrow { align-self: center; color: var(--pc-muted); font-size: 16px; padding: 0 1px; }
  .fanout-group {
    border: 1px dashed var(--pc-border);
    border-radius: 8px;
    padding: 8px;
    background: var(--pc-bg);
    flex: 0 0 165px;
  }
  .fanout-head { font-size: 11px; font-weight: 600; color: var(--pc-muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .04em; }
  .fanout-branches { display: flex; flex-direction: column; gap: 6px; }
  .fanout-branches .node { width: 145px; min-height: 66px; }
  .agentic-flow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .stage {
    border: 1px solid var(--pc-border);
    border-left: 4px solid var(--pc-muted);
    border-radius: 6px;
    padding: 8px 10px;
    background: var(--pc-bg);
    position: relative;
    width: 145px;
    min-height: 66px;
    flex: 0 0 145px;
  }
  .stage .name { font-weight: 600; display: flex; justify-content: space-between; gap: 6px; }
  .stage .meta { font-size: 11px; color: var(--pc-muted); margin-top: 2px; }
  .stage .mini-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
  .mini-chip { border: 1px solid var(--pc-border); border-radius: 999px; padding: 1px 6px; font-size: 10px; color: var(--pc-muted); }
  .external-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .pill { display:inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px; border:1px solid var(--pc-border); }
  button {
    font: inherit; cursor: pointer; border-radius: 6px; padding: 6px 14px;
    border: 1px solid transparent;
    background: var(--pc-accent); color: var(--color-white, #fff); font-weight: 600;
  }
  button.secondary { background: transparent; color: var(--pc-fg); border-color: var(--pc-border); }
  button:disabled { opacity: .55; cursor: default; }
  select, textarea {
    font: inherit; color: inherit; background: var(--pc-bg);
    border: 1px solid var(--pc-border); border-radius: 6px; padding: 6px 8px;
  }
  textarea { width: 100%; min-height: 56px; resize: vertical; margin-top: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--pc-border); vertical-align: top; }
  th { color: var(--pc-muted); font-weight: 600; }
  code { font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace); font-size: 11px; }
  .timeline { display: flex; flex-direction: column; gap: 6px; }
  .ti { border:1px solid var(--pc-border); border-radius:6px; padding:6px 9px; }
  .ti .h { font-weight:600; display:flex; gap:8px; align-items:center; }
  .ti pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-word; font-size:11px; color: var(--pc-muted); }
  .k-call .h { color: var(--pc-blue); }
  .k-output .h { color: var(--pc-green); }
  .k-message .h { color: var(--pc-fg); }
  .banner { padding:8px 12px; border-radius:6px; margin-bottom:10px; font-weight:600; }
  .banner.ok { background: var(--pc-green-tint); border:1px solid var(--pc-green); }
  .banner.bad { background: var(--pc-red-tint); border:1px solid var(--pc-red); }
  .run-live { border:1px solid var(--pc-blue); border-left:4px solid var(--pc-blue); border-radius:8px; padding:10px 12px; margin-top:10px; background:var(--pc-blue-tint); }
  .run-live .h { font-weight:700; display:flex; gap:8px; align-items:center; }
  .run-live .steps { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
  .run-live .steps span { border:1px solid var(--pc-border); border-radius:999px; padding:2px 7px; font-size:11px; background:var(--pc-surface); }
  .run-live .steps span.active { border-color:var(--pc-blue); color:var(--pc-blue); font-weight:700; }
  .run-log { margin-top:8px; display:flex; flex-direction:column; gap:5px; }
  .run-log-row { display:grid; grid-template-columns:58px 145px 1fr; gap:8px; align-items:start; font-size:12px; }
  .run-log-time { color:var(--pc-muted); font-family:var(--font-mono, "SFMono-Regular", Consolas, monospace); }
  .run-log-step { font-weight:700; color:var(--pc-blue); }
  .run-log-detail { color:var(--pc-fg); }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid currentColor; border-right-color:transparent; border-radius:50%; animation:spin .7s linear infinite; vertical-align:-2px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* Workflow animation: which stage/service is doing work right now. */
  .flow .node, .flow .stage { transition: outline-color .2s ease, box-shadow .2s ease; position: relative; }
  .flow .node.working, .flow .stage.working { outline: 2px dashed var(--pc-blue); outline-offset: 1px; animation: pc-pulse 1.1s ease-in-out infinite; }
  .flow .node.calling, .flow .stage.calling { outline: 2px solid var(--pc-blue); outline-offset: 1px; animation: pc-pulse .9s ease-in-out infinite; }
  .flow .node.done-call, .flow .stage.done-call { outline: 2px solid var(--pc-green); outline-offset: 1px; }
  .flow .node.calling .dot, .flow .node.working .dot { background: var(--pc-blue); box-shadow: 0 0 8px var(--pc-blue); }
  @keyframes pc-pulse { 0%,100% { box-shadow: 0 0 0 0 var(--pc-blue-tint); } 50% { box-shadow: 0 0 14px 3px var(--pc-blue); } }
  .flow .node .work-badge, .flow .stage .work-badge { position:absolute; top:-8px; right:-8px; font-size:10px; line-height:1; padding:2px 5px; border-radius:999px; background:var(--pc-blue); color:#fff; font-weight:600; white-space:nowrap; box-shadow:0 1px 3px rgba(0,0,0,.3); }
  .flow .arrow.flowing { color: var(--pc-blue); animation: pc-arrow .8s ease-in-out infinite; }
  @keyframes pc-arrow { 0%,100% { opacity: .45; transform: translateX(0); } 50% { opacity: 1; transform: translateX(3px); } }
</style>
</head>
<body>
<img src="/client-pixel?kind=html_parsed" width="1" height="1" alt="" style="position:absolute;opacity:0;pointer-events:none" />
<noscript>
  <div style="padding:16px;font-family:system-ui;color:var(--pc-fg);background:var(--pc-bg)">
    Pipeline Mission Control loaded, but JavaScript is disabled in this canvas webview.
  </div>
</noscript>
<div class="wrap">
  <h1>Pipeline Mission Control</h1>
  <div class="sub" id="subline">Canvas loaded · connecting to local pipeline state…</div>

  <div class="row" style="margin: 2px 0 6px;">
    <button id="toggleWaypoint" class="secondary">Start Waypoint</button>
    <button id="toggleStack">⏻ Start Forge stack</button>
    <button id="refresh" class="secondary">Refresh</button>
    <span id="diagChip" class="status-chip">Logs: idle</span>
    <span id="spinupStatus" class="muted"></span>
  </div>

  <details class="services" id="serviceDetails" open>
    <summary>
      <span class="section-title">Local services + IQ-tool agents (${IQ_MODE} mode)</span>
      <span id="serviceSummary"></span>
      <span class="summary-hint">details</span>
    </summary>
    <div class="grid" id="board">
      ${NODES.map((n) => `
        <div class="node service-node down">
          <div class="name"><span>${escapeHtml(n.label)}</span><span class="dot"></span></div>
          <div class="meta">
            <div>${escapeHtml(n.role || "")}</div>
            <div class="endpoint">${escapeHtml(n.endpoint ? `:${n.port}${n.endpoint}` : n.detail || `:${n.port}`)}</div>
            <div class="state">checking…</div>
          </div>
        </div>
      `).join("")}
    </div>
  </details>

  <section>
    <div class="section-title">Pacioli workflow</div>
    <div class="flow" id="flow">
      <div class="pacioli-frame frame-down">
        <div class="frame-head">
          <span>Pacioli assurance run</span>
          <span class="frame-meta">loading workflow view…</span>
        </div>
        <div class="boundary-note">Canvas shell is loaded. Live service state will replace this placeholder.</div>
      </div>
    </div>
  </section>

  <section>
    <div class="section-title">Run a pass</div>
    <div class="card">
      <div class="row">
        <label>Target&nbsp;
          <select id="target">
            <option value="pacioli">pacioli workflow</option>
          </select>
        </label>
        <button id="run">Run pipeline</button>
        <span id="runStatus" class="muted"></span>
      </div>
      <textarea id="prompt"></textarea>
    </div>
    <div id="runResult"></div>
  </section>

  <section>
    <div class="section-title">Waypoint result <span class="muted" id="wpSummary"></span></div>
    <div id="waypoint"></div>
  </section>

  <details class="diagnostics" id="diagnostics">
    <summary>
      <span class="section-title">Diagnostics</span>
      <span id="diagSummary" class="muted">command output and log tails</span>
      <span class="summary-hint">details</span>
    </summary>
    <div class="diagnostics-body">
      <div class="diag-toolbar">
        <label>Log&nbsp;<select id="diagService"></select></label>
        <button id="refreshDiagnostics" class="secondary">Refresh logs</button>
        <button id="copyDiagnostics" class="secondary">Copy visible log</button>
        <button id="resetLocalState" class="secondary">Reset local state</button>
      </div>
      <pre class="diag-log" id="diagLog">No diagnostics loaded yet.</pre>
      <div>
        <div class="section-title">Recent commands</div>
        <div class="command-list" id="commandHistory"></div>
      </div>
    </div>
  </details>
</div>

<script>
function reportClientEvent(kind, detail) {
  const payload = {
    kind,
    detail: detail || "",
    href: location.href,
    readyState: document.readyState,
    at: Date.now()
  };
  try {
    const body = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: "application/json" });
      if (navigator.sendBeacon("/client-log", blob)) return;
    }
    fetch("/client-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true
    }).catch(() => {});
  } catch {}
}
reportClientEvent("boot", "script entered");
window.addEventListener("error", (event) => {
  const subline = document.getElementById("subline");
  if (subline) subline.textContent = "Canvas render error: " + (event.message || event.error || "unknown error");
  reportClientEvent("error", event.message || String(event.error || "unknown error"));
});
window.addEventListener("unhandledrejection", (event) => {
  const subline = document.getElementById("subline");
  if (subline) subline.textContent = "Canvas async error: " + (event.reason?.message || event.reason || "unknown error");
  reportClientEvent("unhandledrejection", event.reason?.message || String(event.reason || "unknown error"));
});
const DEFAULT_PROMPT = ${JSON.stringify(DEFAULT_PROMPT)};
const NODES = ${JSON.stringify(NODES)};
const PROMPT_AGENT_NODES = ${JSON.stringify(PROMPT_AGENT_NODES)};
const WORKFLOW_PRE_STAGES = [
  { id: "pacioli", step: "request", label: "Receive request", meta: "parse invoice target" },
  { id: "prepare_run", step: "prepare_run", label: "Prepare run", meta: "run id + attempt context" },
  { id: "discover_work", step: "discover_work", label: "Discover work", meta: "read target invoice(s)" },
  { id: "resolve_documents", step: "resolve_documents", label: "Resolve docs", meta: "collect invoice artifacts" },
  { id: "resolve_context", step: "resolve_context", label: "Resolve context", meta: "Waypoint context read" },
  { id: "build_canonical_invoice_packets", step: "build_canonical_invoice_packets", label: "Build packet", meta: "canonical invoice packet" },
  { id: "deterministic_reconciliation", step: "deterministic_reconciliation", label: "Reconcile", meta: "deterministic checks" },
  { id: "lookup_existing_invoices", step: "lookup_existing_invoices", label: "Lookup invoice", meta: "Waypoint exact lookup" },
  { id: "duplicate_invoice_check", step: "duplicate_invoice_check", label: "Duplicate check", meta: "known duplicate rules" },
  { id: "select_validators", step: "select_validators", label: "Select agents", meta: "deterministic routing to IQ-tool agents" },
];
const WORKFLOW_POST_STAGES = [
  { id: "fan_in_aggregate", step: "fan_in_aggregate", label: "Aggregate", meta: "fan-in agent results" },
  { id: "synthesize_judgement", step: "synthesize_judgement", label: "Synthesize", meta: "evidence-backed judgement" },
  { id: "prepare_waypoint_write_plan", step: "prepare_waypoint_write_plan", label: "Write plan", meta: "read-only future payloads" },
];
const WORKFLOW_STAGES = [
  ...WORKFLOW_PRE_STAGES,
  { id: "fan_out_validators", step: "fan_out_validators", label: "Fan out", meta: "parallel calls to agents with IQ tools" },
  ...WORKFLOW_POST_STAGES,
];
let lastTouched = [];
let runActive = false;
let currentNodes = [];
let currentDiagnostics = null;
document.getElementById("prompt").value = DEFAULT_PROMPT;

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) { if (k === "class") e.className = attrs[k]; else if (k === "html") e.innerHTML = attrs[k]; else e.setAttribute(k, attrs[k]); }
  for (const c of (children||[])) e.append(c);
  return e;
}

function renderBoard(nodes) {
  const summary = document.getElementById("serviceSummary");
  if (summary) {
    summary.innerHTML = "";
    const keyIds = ["waypoint-api", "waypoint-web", "pacioli"];
    for (const id of keyIds) {
      const n = nodeById(nodes, id);
      const label = id === "waypoint-api" ? "Waypoint API"
        : id === "waypoint-web" ? "Waypoint Web"
        : "Pacioli";
      summary.append(el("span", { class: "status-chip " + (n.up ? "ok" : "bad") }, [
        label
        + ": "
        + (n.up ? "up" : "down")
      ]));
    }
    for (const prompt of PROMPT_AGENT_NODES) {
      const n = nodeById(nodes, prompt.id);
      summary.append(el("span", { class: "status-chip " + (n.up ? "ok" : "bad") }, [
        prompt.label + ": " + (n.up ? "up" : "down")
      ]));
    }
  }
  const board = document.getElementById("board");
  board.innerHTML = "";
  for (const n of nodes) {
    const card = el("div", { class: "node service-node " + (n.up ? "up" : "down") + (lastTouched.includes(n.id) ? " touched" : "") });
    const name = el("div", { class: "name" });
    name.append(el("span", null, [n.label]));
    name.append(el("span", { class: "dot" }));
    card.append(name);
    const endpoint = n.endpoint ? ":" + n.port + n.endpoint : n.detail || ":" + n.port;
    card.append(el("div", { class: "meta" }, [
      el("div", null, [n.role]),
      el("div", { class: "endpoint" }, [endpoint]),
      el("div", { class: "state" }, [n.up ? "up" : "down"]),
    ]));
    const controls = el("div", { class: "controls" });
    const inflight = nodeActionsInFlight.get(n.id);
    const mkBtn = (action, label) => {
      const b = el("button", { class: "node-btn", type: "button", "data-node-action": action, "data-node-id": n.id, title: label + " " + n.label }, [label]);
      if (action === "start" && n.up) b.disabled = true;
      if (action === "stop" && !n.up) b.disabled = true;
      if (inflight) {
        b.disabled = true;
        b.classList.add("busy");
        if (inflight === action) b.innerHTML = '<span class="spinner"></span>';
      }
      return b;
    };
    controls.append(mkBtn("start", "Start"));
    controls.append(mkBtn("stop", "Stop"));
    controls.append(mkBtn("restart", "Restart"));
    if (n.id === "waypoint-web" || n.id === "waypoint-api") {
      const open = el("button", { class: "node-btn", type: "button", "data-node-open": n.id, title: "Open " + n.label + " in browser" }, ["↗ Open"]);
      if (!n.up) open.disabled = true;
      controls.append(open);
    }
    card.append(controls);
    board.append(card);
  }
}

function nodeById(nodes, id) { return nodes.find(n => n.id === id) || { up:false }; }
function waypointRunning(nodes) { return !!nodeById(nodes, "waypoint-api").up; }
function forgeStackRunning(nodes) {
  return nodes.some(n => n.group !== "waypoint" && n.up);
}
function updateToggleButtons(nodes) {
  const waypointBtn = document.getElementById("toggleWaypoint");
  const stackBtn = document.getElementById("toggleStack");
  if (waypointBtn) {
    const up = waypointRunning(nodes);
    waypointBtn.textContent = up ? "Stop Waypoint" : "Start Waypoint";
    waypointBtn.classList.toggle("secondary", !up);
  }
  if (stackBtn) {
    const up = forgeStackRunning(nodes);
    stackBtn.textContent = up ? "⏼ Stop Forge stack" : "⏻ Start Forge stack";
    stackBtn.classList.toggle("secondary", up);
  }
}
function chip(nodes, id, label) {
  const n = nodeById(nodes, id);
  return el("div", { id:"flow-"+id, "data-node":id, class: "node " + (n.up ? "up":"down") + (lastTouched.includes(id) ? " touched":"") }, [
    (function(){ const d = el("div",{class:"name"}); d.append(el("span",null,[label||id])); d.append(el("span",{class:"dot"})); return d; })(),
    el("div", { class:"meta" }, [n.detail || (":" + (n.port||"") + (n.endpoint || ""))])
  ]);
}

function workflowStage(stage) {
  const card = el("div", { id:"flow-"+stage.id, "data-node":stage.id, class:"stage" + (lastTouched.includes(stage.id) ? " touched" : "") });
  const name = el("div", { class:"name" });
  name.append(el("span", null, [stage.label]));
  card.append(name);
  card.append(el("div", { class:"meta" }, [stage.meta]));
  return card;
}

function chain(stages) {
  const row = el("div", { class:"workflow-chain" });
  stages.forEach((stage, index) => {
    if (index > 0) row.append(el("div", { class:"workflow-arrow" }, ["→"]));
    row.append(workflowStage(stage));
  });
  return row;
}

function renderFlow(nodes) {
  const flow = document.getElementById("flow");
  flow.innerHTML = "";
  const pacioli = nodeById(nodes, "pacioli");
  const frame = el("div", {
    class: "pacioli-frame " + (pacioli.up ? "frame-up" : "frame-down") + (lastTouched.includes("pacioli") ? " frame-touched" : ""),
  });
  frame.append(el("div", { class:"frame-head" }, [
    el("span", null, ["Pacioli assurance run"]),
    el("span", { class:"frame-meta" }, [":" + (pacioli.port || 8088) + " · deterministic workflow · " + (pacioli.up ? "up" : "down")]),
  ]));
  const fanout = el("div", { class:"fanout-group" }, [
    el("div", { class:"fanout-head" }, ["Agents with IQ-tool fan-out"]),
    el("div", { class:"fanout-branches" }, PROMPT_AGENT_NODES.map(n => chip(nodes, n.id, n.label))),
  ]);
  const body = el("div", { class:"agentic-flow" }, [
    workflowStage({ id: "pacioli", label: "Content understanding", meta: "read context + build packet" }),
    el("div", { class:"workflow-arrow" }, ["→"]),
    fanout,
    el("div", { class:"workflow-arrow" }, ["→"]),
    workflowStage({ id: "fan_in_aggregate", label: "Aggregate", meta: "fan-in agent evidence" }),
    el("div", { class:"workflow-arrow" }, ["→"]),
    workflowStage({ id: "synthesize_judgement", label: "Synthesize", meta: "evidence-backed judgement" }),
    el("div", { class:"workflow-arrow" }, ["→"]),
    workflowStage({ id: "prepare_waypoint_write_plan", label: "Write-plan preview", meta: "read-only Waypoint payloads" }),
  ]);
  frame.append(body);
  frame.append(el("div", { class:"boundary-note" }, [
    "Simplified agentic view: Pacioli coordinates the run, fans out to selected agents that each own an IQ tool, aggregates their evidence, synthesizes the judgement, and prepares a read-only write-plan preview. Waypoint API status is shown in the services section above."
  ]));
  flow.append(frame);
}

// ---------------------------------------------------------------------------
// Fan-out animation. The run is a single synchronous POST, so we can't stream
// per-call events live; instead we show a generic "working" pulse while the
// request is in flight, then REPLAY the real fan-out (in the exact order the
// agents were actually called) once the result returns.
// ---------------------------------------------------------------------------
let workingTimer = null;
let workingIndex = 0;
let runProgressTimer = null;
let runStartedAt = 0;

function flowNode(id) { return document.getElementById("flow-" + id); }
function setArrow(idx, on) { const a = document.getElementById("arrow-" + idx); if (a) a.classList.toggle("flowing", !!on); }

function setWork(id, state, label) {
  const e = flowNode(id);
  if (!e) return;
  e.classList.remove("working","calling","done-call");
  const old = e.querySelector(".work-badge");
  if (old) old.remove();
  if (state) e.classList.add(state);
  if (label && (state === "calling" || state === "working")) {
    e.append(el("span",{class:"work-badge"},[label]));
  }
}

function clearFlowAnim() {
  document.querySelectorAll(".flow .working,.flow .calling,.flow .done-call")
    .forEach(e => e.classList.remove("working","calling","done-call"));
  document.querySelectorAll(".flow .work-badge").forEach(e => e.remove());
  setArrow(1,false); setArrow(2,false); setArrow(3,false);
}

function startFlowWorking() {
  clearFlowAnim();
  workingIndex = 0;
  const sequence = [
    "pacioli",
    ...PROMPT_AGENT_NODES.map(n => n.id),
    ...WORKFLOW_POST_STAGES.map(s => s.id),
  ];
  const tick = () => {
    clearFlowAnim();
    const id = sequence[workingIndex % sequence.length];
    setWork(id, "calling", "running");
    if (PROMPT_AGENT_NODES.some(n => n.id === id)) {
      for (const n of PROMPT_AGENT_NODES) {
        if (n.id !== id) {
          const e = flowNode(n.id);
          if (e) e.classList.add("working");
        }
      }
    }
    workingIndex += 1;
  };
  tick();
  workingTimer = setInterval(tick, 900);
}

function progressLabel(seconds) {
  const steps = [
    "content understanding",
    "agent fan-out",
    "aggregate",
    "synthesize",
    "write-plan preview",
  ];
  return steps[Math.min(steps.length - 1, Math.floor(seconds / 8) % steps.length)];
}

function renderRunProgress(target) {
  const seconds = Math.max(0, Math.floor((Date.now() - runStartedAt) / 1000));
  const label = progressLabel(seconds);
  const box = document.getElementById("runResult");
  box.innerHTML = "";
  box.append(el("div", { class:"run-live" }, [
    el("div", { class:"h" }, [
      el("span", { class:"spinner" }),
      "Running " + target + " · " + seconds + "s elapsed · " + label
    ]),
    el("div", { class:"muted" }, ["This is live progress; exact agent/tool results appear when Pacioli returns."]),
    el("div", { class:"steps" }, [
      "content understanding",
      "agent fan-out",
      "aggregate",
      "synthesize",
      "write-plan preview",
    ].map(step => el("span", { class: step === label ? "active" : "" }, [step]))),
  ]));
  const status = document.getElementById("runStatus");
  if (status) status.innerHTML = '<span class="spinner"></span> ' + seconds + 's · ' + label;
}

function renderLiveJob(job) {
  const box = document.getElementById("runResult");
  const seconds = Math.max(0, Math.floor((job.elapsedMs || 0) / 1000));
  const events = Array.isArray(job.events) ? job.events.slice(-10).reverse() : [];
  box.innerHTML = "";
  box.append(el("div", { class:"run-live" }, [
    el("div", { class:"h" }, [
      el("span", { class:"spinner" }),
      "Running " + (job.target || "pacioli") + " · " + seconds + "s elapsed · " + (job.step || "running")
    ]),
    el("div", { class:"muted" }, ["Live checkpoints from the canvas provider while Pacioli executes."]),
    el("div", { class:"run-log" }, events.map((event) => el("div", { class:"run-log-row" }, [
      el("div", { class:"run-log-time" }, [Math.max(0, Math.round((event.elapsedMs || 0) / 1000)) + "s"]),
      el("div", { class:"run-log-step" }, [event.step || "event"]),
      el("div", { class:"run-log-detail" }, [event.detail || ""])
    ]))),
  ]));
  const status = document.getElementById("runStatus");
  if (status) status.innerHTML = '<span class="spinner"></span> ' + seconds + 's · ' + (job.step || "running");
}

async function pollRunJob(jobId, target) {
  for (let attempt = 0; attempt < 360; attempt++) {
    const response = await fetch("/run-status?id=" + encodeURIComponent(jobId), { cache: "no-store" });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || ("run-status failed with HTTP " + response.status));
    if (job.error && !job.status) throw new Error(job.error);
    if (job.status === "running") {
      renderLiveJob(job);
      await sleep(1000);
      continue;
    }
    if (job.status === "completed") {
      return job.result;
    }
    throw new Error(job.error || "run failed");
  }
  throw new Error("Timed out waiting for run status.");
}

function startRunProgress(target) {
  stopRunProgress(false);
  runStartedAt = Date.now();
  renderRunProgress(target);
  runProgressTimer = setInterval(() => renderRunProgress(target), 1000);
}

function stopRunProgress(clear = true) {
  if (runProgressTimer) {
    clearInterval(runProgressTimer);
    runProgressTimer = null;
  }
  if (clear) {
    const box = document.getElementById("runResult");
    if (box) box.innerHTML = "";
  }
}

function stopFlowWorking() {
  if (workingTimer) { clearInterval(workingTimer); workingTimer = null; }
  for (const id of [
    ...WORKFLOW_STAGES.map(s => s.id),
    ...PROMPT_AGENT_NODES.map(n => n.id),
    "waypoint-api",
  ]) {
    const e = flowNode(id);
    if (e) e.classList.remove("working");
  }
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function formatTime(ts) {
  return ts ? new Date(ts).toLocaleTimeString() : "";
}

function setDiagChip(text, bad) {
  const chip = document.getElementById("diagChip");
  chip.textContent = text;
  chip.classList.toggle("bad", !!bad);
  chip.classList.toggle("ok", !bad && text !== "Logs: idle");
}

function renderDiagnostics(data, preferredService) {
  currentDiagnostics = data;
  const logs = Array.isArray(data.logs) ? data.logs : [];
  const commands = Array.isArray(data.commands) ? data.commands : [];
  const select = document.getElementById("diagService");
  const selected = preferredService || select.value || logs.find(l => l.exists)?.id || logs[0]?.id || "";
  select.innerHTML = "";
  for (const log of logs) {
    const opt = document.createElement("option");
    opt.value = log.id;
    opt.textContent = log.label + (log.exists ? "" : " (empty)");
    select.append(opt);
  }
  select.value = logs.some(l => l.id === selected) ? selected : (logs[0]?.id || "");
  const active = logs.find(l => l.id === select.value);
  document.getElementById("diagLog").textContent = active?.tail || "No log output captured yet for " + (active?.label || "this service") + ".";
  const failed = commands.some(c => c.status === "failed" || c.status === "warning");
  const newest = commands[0];
  const canvasEvents = Array.isArray(data.canvas?.events) ? data.canvas.events : [];
  const newestCanvas = canvasEvents[0];
  document.getElementById("diagSummary").textContent = newest
    ? newest.status + " · " + newest.service + " · " + formatTime(newest.ts)
    : newestCanvas
      ? "canvas " + newestCanvas.kind + " · " + formatTime(newestCanvas.ts)
    : "command output and log tails";
  setDiagChip(newest ? ("Logs: " + newest.status) : "Logs: idle", failed);
  const history = document.getElementById("commandHistory");
  history.innerHTML = "";
  if (canvasEvents.length) {
    history.append(el("div", { class:"section-title" }, ["Canvas lifecycle"]));
    for (const event of canvasEvents.slice(0, 8)) {
      const row = el("div", { class:"command-row" });
      row.append(el("div", null, [
        el("span", { class:"pill" }, [event.source || "canvas"]),
        " ",
        el("span", { class:"muted" }, [formatTime(event.ts) + " · " + (event.kind || "event")])
      ]));
      const detail = [
        event.instanceId ? "instance=" + event.instanceId : "",
        event.url || "",
        event.detail || "",
        event.readyState ? "readyState=" + event.readyState : "",
      ].filter(Boolean).join("\\n");
      if (detail) row.append(el("div", { class:"diag-output" }, [detail.slice(0, 1200)]));
      history.append(row);
    }
  }
  if (!commands.length) {
    if (canvasEvents.length) return;
    history.append(el("div", { class:"muted" }, ["No commands captured yet."]));
    return;
  }
  if (canvasEvents.length) history.append(el("div", { class:"section-title" }, ["Recent commands"]));
  for (const cmd of commands.slice(0, 8)) {
    const row = el("div", { class:"command-row" });
    row.append(el("div", null, [
      el("span", { class:"pill" }, [cmd.status || "unknown"]),
      " ",
      el("span", { class:"muted" }, [formatTime(cmd.ts) + " · " + (cmd.service || "service")])
    ]));
    row.append(el("div", { class:"cmd" }, [cmd.command || "command unavailable"]));
    const output = [cmd.stdout, cmd.stderr, cmd.error].filter(Boolean).join("\\n");
    if (output) row.append(el("div", { class:"diag-output" }, [output.slice(0, 1200)]));
    history.append(row);
  }
}

async function refreshDiagnostics(open = false, preferredService = "") {
  const response = await fetch("/diagnostics", { cache: "no-store" });
  const data = await response.json();
  renderDiagnostics(data, preferredService);
  if (open) document.getElementById("diagnostics").open = true;
}

async function showDiagnosticsForFailure(service) {
  try {
    await refreshDiagnostics(true, service || "");
  } catch {
    document.getElementById("diagnostics").open = true;
  }
}

async function replayFanout(result) {
  clearFlowAnim();
  const calls = (result.items || []).filter(it => it.kind === "call" && it.node);
  const touched = result.touched || [];
  const touchedPacioli = calls.some(c => c.node === "pacioli") || touched.includes("pacioli");
  const touchedWaypoint = (result.runDelta || 0) > 0 || touched.includes("waypoint-api");
  const touchedValidators = PROMPT_AGENT_NODES.map(n => n.id).filter(id => touched.includes(id) || calls.some(c => c.node === id));

  setWork("pacioli", "calling", "routing");
  await sleep(500);

  if (touchedPacioli) {
    for (const id of touchedValidators.length ? touchedValidators : PROMPT_AGENT_NODES.map(n => n.id)) {
      setWork(id, "calling", "validating");
      await sleep(450);
      setWork(id, "done-call");
    }
  }

  for (const stage of WORKFLOW_POST_STAGES) {
    setWork(stage.id, "calling", "running");
    if (stage.id === "prepare_waypoint_write_plan") setWork("waypoint-api","calling","preview");
    await sleep(420);
    setWork(stage.id, "done-call");
    if (stage.id === "prepare_waypoint_write_plan" && touchedWaypoint) setWork("waypoint-api","done-call");
  }

  setWork("pacioli","done-call");
  await sleep(2000);
  clearFlowAnim();
}

function renderWaypoint(wp) {
  const box = document.getElementById("waypoint");
  const sum = document.getElementById("wpSummary");
  box.innerHTML = "";
  if (!wp || !wp.reachable) { sum.textContent = "— API unreachable"; box.append(el("div",{class:"muted"},["Start Waypoint (Postgres → API :8000 → web :5173) to see cases."])); return; }
  sum.textContent = "· " + wp.runs + " runs · " + wp.cases.length + " cases · " + wp.audit.length + " audit events";
  if (wp.cases.length) {
    const t = el("table");
    t.append(el("tr", null, [el("th",null,["case"]), el("th",null,["invoice"]), el("th",null,["status"]), el("th",null,["run"])]));
    for (const c of wp.cases.slice(0,8)) {
      const runId = (c.metadata && c.metadata.waypoint_run_id) || "";
      t.append(el("tr", null, [
        el("td",null,[el("code",null,[String(c.id||"").slice(0,18)])]),
        el("td",null,[c.invoice_id||""]),
        el("td",null,[el("span",{class:"pill"},[c.status||""])]),
        el("td",null,[el("code",null,[String(runId).slice(0,18)])]),
      ]));
    }
    box.append(t);
  } else {
    box.append(el("div",{class:"muted"},["No cases yet — run a pass."]));
  }
  if (wp.audit.length) {
    const tl = el("div",{class:"timeline"});
    for (const a of wp.audit.slice(0,6)) {
      tl.append(el("div",{class:"ti k-output"},[ el("div",{class:"h"},[ a.event_type||a.eventType||"event", el("span",{class:"muted"},[" · "+(a.actor||"")+" · "+(a.auth_source||"")]) ]) ]));
    }
    box.append(el("div",{style:"margin-top:10px"},["Audit timeline"]));
    box.append(tl);
  }
}

async function poll() {
  try {
    const r = await fetch("/state"); const s = await r.json();
    reportClientEvent("state_ok", "nodes=" + (Array.isArray(s.nodes) ? s.nodes.length : "unknown"));
    currentNodes = s.nodes;
    renderBoard(s.nodes);
    updateToggleButtons(s.nodes);
    if (!runActive) renderFlow(s.nodes);
    renderWaypoint(s.waypoint);
    const upCount = s.nodes.filter(n => n.up).length;
    document.getElementById("subline").textContent =
      upCount + "/" + s.nodes.length + " nodes up · refreshed " + new Date(s.ts).toLocaleTimeString();
  } catch (e) {
    document.getElementById("subline").textContent = "state error: " + e;
    reportClientEvent("state_error", String(e?.message || e));
  }
}

function renderRun(result) {
  const box = document.getElementById("runResult");
  box.innerHTML = "";
  if (result.error) { box.append(el("div",{class:"banner bad"},["Run failed: " + result.error])); return; }
  lastTouched = (result.touched||[]).slice();
  const ok = result.runDelta > 0;
  const preview = result.readOnly || result.sideEffectsPerformed === false;
  const healthy = ok || preview || (result.httpStatus >= 200 && result.httpStatus < 300);
  const headline = ok
    ? "✓ Wrote " + result.runDelta + " run(s) to Waypoint"
    : preview
      ? "✓ Prepared read-only write plan"
      : "⚠ No Waypoint write detected (runDelta 0)";
  const banner = el("div", { class: "banner " + (ok?"ok":"bad") }, [
    headline
    + " · " + (result.touched.length ? "fan-out: " + result.touched.join(", ") : "no tool calls")
    + (result.validatorCount != null ? " · IQ-tool agents " + result.validatorCount : "")
    + (result.futurePayloadCount != null ? " · future payloads " + result.futurePayloadCount : "")
    + " · HTTP " + result.httpStatus + " · " + (result.elapsedMs/1000).toFixed(1) + "s"
  ]);
  banner.className = "banner " + (healthy ? "ok" : "bad");
  box.append(banner);
  const tl = el("div",{class:"timeline"});
  for (const it of result.items) {
    const cls = "ti k-" + (it.kind==="call"?"call":it.kind==="output"?"output":it.kind==="message"?"message":"reasoning");
    const head = el("div",{class:"h"});
    if (it.kind==="call") { head.append("→ " + it.name); if (it.node) head.append(el("span",{class:"pill"},[it.node])); }
    else head.append(it.kind);
    const node = el("div",{class:cls},[head]);
    if (it.text) node.append(el("pre",null,[it.text]));
    tl.append(node);
  }
  box.append(tl);
  poll();
}

document.getElementById("toggleStack").addEventListener("click", async () => {
  const btn = document.getElementById("toggleStack");
  const shouldStop = forgeStackRunning(currentNodes);
  const st = document.getElementById("spinupStatus");
  btn.disabled = true;
  st.innerHTML = shouldStop
    ? '<span class="spinner"></span> stopping Forge stack…'
    : '<span class="spinner"></span> starting Forge stack… agents take ~1–2 min';
  try {
    const r = await fetch(shouldStop ? "/stop-stack" : "/start-stack", { method: "POST" });
    const rep = await r.json();
    if (rep.error) {
      st.textContent = "error: " + rep.error;
      await showDiagnosticsForFailure(shouldStop ? "prompt-validators" : "pacioli");
    } else if (shouldStop) {
      let msg = (rep.stopped ? rep.stopped.length : 0) + " stopped";
      if (rep.notRunning && rep.notRunning.length) msg += " · " + rep.notRunning.length + " not running";
      if (rep.stillUp && rep.stillUp.length) msg += " · still up: " + rep.stillUp.join(", ");
      st.textContent = msg;
      if (rep.stillUp && rep.stillUp.length) await showDiagnosticsForFailure(rep.stillUp[0]);
    } else {
      let msg = (rep.upCount || 0) + "/" + (rep.total || 0) + " services up";
      if (rep.localIqMode) msg += " · IQ mode: " + rep.localIqMode;
      if (rep.waypointApiBaseUrl) msg += " · Waypoint API: " + rep.waypointApiBaseUrl;
      if (rep.failed && rep.failed.length) {
        msg += " · failed: " + rep.failed.map((f) => f.reason ? f.id + " (" + f.reason + ")" : f.id).join("; ");
      }
      if (rep.alreadyUp && rep.alreadyUp.length) msg += " · already up: " + rep.alreadyUp.join(", ");
      if (rep.foundryEndpointSet === false) msg += " · FOUNDRY_PROJECT_ENDPOINT not set — agents can't start";
      if (rep.waypointManagedBy) msg += " · Waypoint observed only (" + rep.waypointManagedBy + ")";
      st.textContent = msg;
      if (rep.failed && rep.failed.length) await showDiagnosticsForFailure(rep.failed[0].id);
    }
    poll();
  } catch (e) {
    st.textContent = "error: " + e;
    await showDiagnosticsForFailure(shouldStop ? "prompt-validators" : "pacioli");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("refresh").addEventListener("click", () => poll());

// Per-node Start / Stop / Restart via event delegation on the board.
const nodeActionsInFlight = new Map(); // node id -> action, survives 3s auto-poll rebuilds
async function controlNodeAction(id, action, btn) {
  if (nodeActionsInFlight.has(id)) return;
  nodeActionsInFlight.set(id, action);
  const card = btn.closest(".service-node");
  const btns = card ? card.querySelectorAll(".node-btn") : [btn];
  const st = document.getElementById("spinupStatus");
  const original = btn.textContent;
  btns.forEach((b) => { b.disabled = true; b.classList.add("busy"); });
  btn.innerHTML = '<span class="spinner"></span>';
  const verb = action === "start" ? "starting" : action === "stop" ? "stopping" : "restarting";
  if (st) st.innerHTML = '<span class="spinner"></span> ' + verb + " " + id + "…";
  try {
    const r = await fetch("/control-node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, action }),
    });
    const rep = await r.json();
    if (rep.error) {
      if (st) st.textContent = id + " " + action + " failed: " + rep.error;
      if (action !== "stop") await showDiagnosticsForFailure(id);
    } else if (st) {
      st.textContent = id + " " + action + ": now " + (rep.up ? "up" : "down")
        + (rep.shared ? " (shared stub host '" + rep.shared + "')" : "")
        + (rep.ok ? "" : " (unexpected)");
    }
  } catch (e) {
    if (st) st.textContent = id + " " + action + " error: " + e;
  } finally {
    nodeActionsInFlight.delete(id);
    try { btn.textContent = original; } catch (_) {}
    await poll();
  }
}
document.getElementById("board").addEventListener("click", (event) => {
  const openBtn = event.target.closest("[data-node-open]");
  if (openBtn) {
    if (openBtn.disabled) return;
    const id = openBtn.getAttribute("data-node-open");
    const st = document.getElementById("spinupStatus");
    fetch("/open-external", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }).then((r) => r.json()).then((rep) => {
      if (st) st.textContent = rep.ok ? "opened " + (rep.url || id) + " in browser" : "open failed: " + (rep.error || "unknown");
    }).catch((e) => { if (st) st.textContent = "open error: " + e; });
    return;
  }
  const btn = event.target.closest("[data-node-action]");
  if (!btn || btn.disabled || btn.classList.contains("busy")) return;
  controlNodeAction(btn.getAttribute("data-node-id"), btn.getAttribute("data-node-action"), btn);
});

document.getElementById("refreshDiagnostics").addEventListener("click", () => refreshDiagnostics(true));
document.getElementById("diagService").addEventListener("change", () => {
  if (currentDiagnostics) renderDiagnostics(currentDiagnostics, document.getElementById("diagService").value);
});
document.getElementById("copyDiagnostics").addEventListener("click", async () => {
  const text = document.getElementById("diagLog").textContent || "";
  try { await navigator.clipboard.writeText(text); } catch {}
});
document.getElementById("resetLocalState").addEventListener("click", async () => {
  const btn = document.getElementById("resetLocalState");
  const st = document.getElementById("spinupStatus");
  btn.disabled = true;
  st.innerHTML = '<span class="spinner"></span> resetting local state…';
  try {
    const r = await fetch("/reset-local-state", { method: "POST" });
    const rep = await r.json();
    if (rep.error) {
      st.textContent = "reset failed: " + rep.error;
      await showDiagnosticsForFailure();
      return;
    }
    lastTouched = [];
    currentDiagnostics = null;
    document.getElementById("diagLog").textContent = "Logs cleared.";
    document.getElementById("commandHistory").innerHTML = "";
    setDiagChip("Logs: idle", false);
    st.textContent = "reset complete · stopped services and cleared diagnostics";
    await poll();
    await refreshDiagnostics(true);
  } catch (e) {
    st.textContent = "reset failed: " + e;
    await showDiagnosticsForFailure();
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("toggleWaypoint").addEventListener("click", async () => {
  const btn = document.getElementById("toggleWaypoint");
  const shouldStop = waypointRunning(currentNodes);
  const st = document.getElementById("spinupStatus");
  btn.disabled = true;
  st.innerHTML = shouldStop
    ? '<span class="spinner"></span> stopping Waypoint…'
    : '<span class="spinner"></span> starting Waypoint (Postgres → API → web)…';
  try {
    const r = await fetch(shouldStop ? "/stop-waypoint" : "/start-waypoint", { method: "POST" });
    const rep = await r.json();
    if (rep.error) {
      st.textContent = "error: " + rep.error;
      await showDiagnosticsForFailure("waypoint");
    } else if (rep.failed && rep.failed.length) {
      st.textContent = "Waypoint start failed: " + rep.failed.map(f => f.reason || f.id).join("; ");
      await showDiagnosticsForFailure("waypoint");
    } else {
      const status = rep.status || {};
      const up = Object.keys(status).filter(k => status[k]).join(", ") || "none yet";
      const startedAny = Array.isArray(rep.started) && rep.started.length;
      st.textContent = shouldStop
        ? ("Waypoint " + (rep.status && rep.status["waypoint-api"] ? "still appears up" : "stopped"))
        : ((startedAny ? "Waypoint started" : "Waypoint already up")
        + " · up: " + up
        + (rep.waypointApiBaseUrl ? " · API: " + rep.waypointApiBaseUrl : ""));
    }
    poll();
  } catch (e) {
    st.textContent = "error: " + e;
    await showDiagnosticsForFailure("waypoint");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("run").addEventListener("click", async () => {
  const btn = document.getElementById("run");
  const status = document.getElementById("runStatus");
  const target = document.getElementById("target").value;
  const message = document.getElementById("prompt").value;
  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span> running ' + target + '… (may take a minute)';
  runActive = true;
  renderFlow(currentNodes);
  startFlowWorking();
  try {
    const r = await fetch("/run", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ target, message }) });
    const started = await r.json();
    if (started.error) throw new Error(started.error);
    renderLiveJob(started);
    const result = await pollRunJob(started.id, target);
    stopFlowWorking();
    renderRun(result);
    if (!result.error) await replayFanout(result);
    status.textContent = "done";
  } catch (e) {
    stopRunProgress();
    stopFlowWorking();
    clearFlowAnim();
    document.getElementById("runResult").innerHTML = "";
    status.textContent = "error: " + e;
    await showDiagnosticsForFailure("pacioli");
  } finally {
    runActive = false;
    btn.disabled = false;
    setTimeout(() => { status.textContent = ""; }, 4000);
  }
});

poll();
refreshDiagnostics(false);
setTimeout(() => {
  const subline = document.getElementById("subline");
  if (subline && /connecting to local pipeline state/.test(subline.textContent || "")) {
    subline.textContent = "Canvas shell loaded, but state has not rendered yet. Open Diagnostics for client/provider events.";
    reportClientEvent("boot_watchdog", "state did not render within 5s");
    refreshDiagnostics(true).catch(() => {});
  }
}, 5000);
setInterval(poll, 3000);
</script>
</body>
</html>`;
