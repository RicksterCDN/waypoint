// Extension: activity-card
// Preview the invoice-analyst Adaptive Card via the local /api/messages
// activity protocol.
//
// The AI Teammate surface (/api/messages) is asynchronous, Bot Framework style:
// the agent ACKs the inbound Activity with 202 and then POSTs its reply (with
// the Adaptive Card attachment) back to the `serviceUrl` we advertise, at
// `{serviceUrl}/v3/conversations/{conversationId}/activities`.
//
// So each open canvas instance runs ONE loopback server that plays two roles:
//   1. Serves the chat UI + client-side card renderer (see ui.mjs).
//   2. Acts as the Bot Framework "connector" that catches the agent's reply.
// A /send call from the UI mints a conversation, POSTs the Activity to the
// agent, and parks the HTTP response until the matching connector callback
// arrives (or it times out) — then returns { text, attachments } to the UI.

import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { joinSession, createCanvas, CanvasError } from "@github/copilot-sdk/extension";
import { renderHtml } from "./ui.mjs";

const DEFAULT_ENDPOINT = "http://127.0.0.1:8090/api/messages";
// How long to wait for the agent's async reply callback. A full FoundryIQ +
// WaypointIQ fan-out can take a while, so keep this generous.
const REPLY_TIMEOUT_MS = 180000;
// Timeout for the inbound Activity POST. The anonymous /api/messages handler
// runs the model synchronously and only returns 202 AFTER it has posted the
// reply back to our connector, so the inbound request stays open for the whole
// turn — give it the same generous budget (plus a small buffer) as the reply.
const POST_TIMEOUT_MS = REPLY_TIMEOUT_MS + 15000;

// One server per open canvas instance. Each carries its own pending-reply map
// so callbacks for that instance's serviceUrl resolve the right request.
const servers = new Map(); // instanceId -> { server, url, pending: Map<convId, {resolve, timer}> }

function sessionLog(session, message, level = "info") {
    try {
        session.log(message, { level });
    } catch {
        /* logging must never throw */
    }
}

function readBody(req) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let size = 0;
        req.on("data", (c) => {
            size += c.length;
            if (size > 5_000_000) {
                reject(new Error("request body too large"));
                req.destroy();
                return;
            }
            chunks.push(c);
        });
        req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
        req.on("error", reject);
    });
}

function json(res, status, obj) {
    const body = JSON.stringify(obj);
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
    res.end(body);
}

// Normalize whatever endpoint the user typed to the /api/messages URL.
function messagesUrl(endpoint) {
    let e = String(endpoint || DEFAULT_ENDPOINT).trim().replace(/\/+$/, "");
    if (e.endsWith("/api/messages")) return e;
    if (e.endsWith("/responses")) e = e.slice(0, -"/responses".length);
    return e + "/api/messages";
}

// Drive one turn: POST an Activity to the agent, then wait for the connector
// callback that carries the reply (text + card attachments).
async function sendTurn(entry, endpoint, message, session) {
    const target = messagesUrl(endpoint);
    const conversationId = randomUUID();
    const activity = {
        type: "message",
        id: randomUUID(),
        timestamp: new Date().toISOString(),
        channelId: "activity-card-canvas",
        serviceUrl: entry.url.replace(/\/+$/, ""),
        conversation: { id: conversationId },
        from: { id: "canvas-user", name: "Canvas User" },
        recipient: { id: "assurance-analyst", name: "Assurance Analyst" },
        text: message,
    };

    const wait = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            entry.pending.delete(conversationId);
            reject(
                new Error(
                    "Timed out waiting for the agent's card reply. Is the agent running and reachable at " +
                        target +
                        "?",
                ),
            );
        }, REPLY_TIMEOUT_MS);
        entry.pending.set(conversationId, { resolve, timer });
    });

    const controller = new AbortController();
    const postTimer = setTimeout(() => controller.abort(), POST_TIMEOUT_MS);
    let ack;
    try {
        ack = await fetch(target, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(activity),
            signal: controller.signal,
        });
    } catch (e) {
        const p = entry.pending.get(conversationId);
        if (p) clearTimeout(p.timer);
        entry.pending.delete(conversationId);
        throw new Error("Could not reach the agent at " + target + " (" + (e.message || e) + ")");
    } finally {
        clearTimeout(postTimer);
    }

    if (ack.status >= 400) {
        const p = entry.pending.get(conversationId);
        if (p) clearTimeout(p.timer);
        entry.pending.delete(conversationId);
        throw new Error("Agent returned HTTP " + ack.status + " for the inbound activity.");
    }
    sessionLog(session, "activity-card: activity posted to " + target + ", awaiting reply", "debug");
    return wait; // resolves to { text, attachments }
}

function makeRequestHandler(instanceId, session) {
    return async (req, res) => {
        const entry = servers.get(instanceId);
        const url = new URL(req.url, "http://127.0.0.1");
        const pathname = url.pathname;

        // Bot Framework connector callback: the agent posts its reply here.
        const convMatch = pathname.match(/^\/v3\/conversations\/([^/]+)\/activities/);
        if (req.method === "POST" && convMatch) {
            const convId = decodeURIComponent(convMatch[1]);
            let activity = {};
            try {
                activity = JSON.parse((await readBody(req)) || "{}");
            } catch {
                /* ignore malformed callback body */
            }
            const pending = entry && entry.pending.get(convId);
            if (pending) {
                entry.pending.delete(convId);
                clearTimeout(pending.timer);
                pending.resolve({
                    text: activity.text || "",
                    attachments: activity.attachments || [],
                });
            }
            // Bot Framework expects a ResourceResponse with an id.
            return json(res, 200, { id: randomUUID() });
        }

        // UI -> "send this message and give me the card reply".
        if (req.method === "POST" && pathname === "/send") {
            let payload = {};
            try {
                payload = JSON.parse((await readBody(req)) || "{}");
            } catch {
                return json(res, 400, { error: "invalid JSON body" });
            }
            const message = String(payload.message || "").trim();
            if (!message) return json(res, 400, { error: "message is required" });
            try {
                const reply = await sendTurn(entry, payload.endpoint, message, session);
                return json(res, 200, reply);
            } catch (e) {
                return json(res, 502, { error: String(e.message || e) });
            }
        }

        if (req.method === "GET" && pathname === "/health") {
            return json(res, 200, { status: "ok" });
        }

        // Everything else -> the chat UI.
        if (req.method === "GET") {
            res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
            return res.end(renderHtml(instanceId, DEFAULT_ENDPOINT));
        }

        res.writeHead(405);
        res.end();
    };
}

async function startServer(instanceId, session) {
    const server = createServer();
    const pending = new Map();
    const entry = { server, url: "", pending };
    servers.set(instanceId, entry);
    server.on("request", makeRequestHandler(instanceId, session));
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    entry.url = `http://127.0.0.1:${port}/`;
    return entry;
}

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "activity-card",
            displayName: "Adaptive Card preview",
            description:
                "Preview the invoice-analyst Adaptive Card rendered by the Teams / AI Teammate activity protocol against a local bridge to the hosted Foundry agent (/api/messages). Renders the real card attachment the agent replies with.",
            inputSchema: {
                type: "object",
                properties: {
                    endpoint: {
                        type: "string",
                        description:
                            "Agent messages endpoint, e.g. http://127.0.0.1:8090/api/messages. Optional; defaults to the local Foundry bridge port.",
                    },
                },
                additionalProperties: false,
            },
            actions: [
                {
                    name: "health",
                    description: "Report whether the canvas server for this instance is running.",
                    handler: async (ctx) => {
                        const entry = servers.get(ctx.instanceId);
                        if (!entry) throw new CanvasError("not_open", "Canvas instance is not open.");
                        return { ok: true, url: entry.url, pending: entry.pending.size };
                    },
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) entry = await startServer(ctx.instanceId, session);
                return { title: "Adaptive Card preview", url: entry.url };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (!entry) return;
                servers.delete(ctx.instanceId);
                for (const p of entry.pending.values()) clearTimeout(p.timer);
                entry.pending.clear();
                await new Promise((resolve) => entry.server.close(() => resolve()));
            },
        }),
    ],
});

sessionLog(session, "activity-card canvas ready");
