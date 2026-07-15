// Extension: onelake-corpus-explorer
// Live browser for the Waypoint demo corpus that the keystone one-click deploy pushed
// into a Microsoft Fabric OneLake lakehouse ("corpus"). Reads the OneLake DFS endpoint
// directly (ADLS Gen2 List/Read), minting an AAD token via the Azure CLI. Shows the live
// Files/ document tree (with content preview) and the Tables/ Delta tables (live schema +
// row counts parsed from the Delta transaction log).
//
// Auth: the signed-in `az` user must be a MEMBER of the Fabric workspace. Until then the
// canvas renders an "access needed" panel with the exact grant to make.

import { createServer } from "node:http";
import { request as httpsRequest } from "node:https";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { joinSession, createCanvas, CanvasError } from "@github/copilot-sdk/extension";

// ---- Fabric / OneLake target (set by the keystone ledgerfield-onelake-upload job) ----
const WORKSPACE = process.env.ONELAKE_WORKSPACE || "";
const LAKEHOUSE_NAME = process.env.ONELAKE_LAKEHOUSE || "corpus";
const CORPUS_PREFIX = process.env.ONELAKE_CORPUS_PREFIX || "Files/corpus";
const ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com";
const FABRIC_API = "https://api.fabric.microsoft.com/v1";
const STORAGE_RESOURCE = "https://storage.azure.com";
const FABRIC_RESOURCE = "https://api.fabric.microsoft.com";

const servers = new Map(); // instanceId -> { server, url }

// ---------------------------------------------------------------------------
// Azure CLI token minting (cached per-resource until ~2 min before expiry)
// ---------------------------------------------------------------------------
const AZ = ["/opt/homebrew/bin/az", "/usr/local/bin/az", "/opt/az/bin/az"].find((p) =>
    existsSync(p)
) || "az";

const tokenCache = new Map(); // resource -> { token, exp }

function runAz(args) {
    return new Promise((resolve, reject) => {
        execFile(
            AZ,
            args,
            { maxBuffer: 16 * 1024 * 1024, env: { ...process.env, PATH: `/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || ""}` } },
            (err, stdout, stderr) => {
                if (err) reject(new Error((stderr || err.message || "").trim()));
                else resolve(stdout);
            }
        );
    });
}

async function getToken(resource) {
    const cached = tokenCache.get(resource);
    const now = Math.floor(Date.now() / 1000);
    if (cached && cached.exp - 120 > now) return cached.token;
    const out = await runAz([
        "account",
        "get-access-token",
        "--resource",
        resource,
        "-o",
        "json",
    ]);
    const j = JSON.parse(out);
    const token = j.accessToken;
    const exp = Number(j.expires_on) || now + 1800;
    tokenCache.set(resource, { token, exp });
    return token;
}

// ---------------------------------------------------------------------------
// OneLake DFS (ADLS Gen2) REST helpers
// ---------------------------------------------------------------------------
function onelakeGet(pathWithQuery, token, { raw = false } = {}) {
    return new Promise((resolve, reject) => {
        const req = httpsRequest(
            {
                host: ONELAKE_HOST,
                path: pathWithQuery,
                method: "GET",
                headers: { Authorization: `Bearer ${token}` },
            },
            (res) => {
                const chunks = [];
                res.on("data", (c) => chunks.push(c));
                res.on("end", () => {
                    const buf = Buffer.concat(chunks);
                    if (res.statusCode >= 200 && res.statusCode < 300) {
                        resolve(raw ? buf : { status: res.statusCode, body: buf.toString("utf8") });
                    } else {
                        let code = "";
                        try {
                            code = JSON.parse(buf.toString("utf8")).error?.code || "";
                        } catch {}
                        reject(
                            Object.assign(new Error(`OneLake ${res.statusCode} ${code}`), {
                                status: res.statusCode,
                                code,
                                body: buf.toString("utf8").slice(0, 400),
                            })
                        );
                    }
                });
            }
        );
        req.on("error", reject);
        req.end();
    });
}

async function listPaths(token, directory, recursive) {
    const q = `/${WORKSPACE}?resource=filesystem&recursive=${recursive ? "true" : "false"}&directory=${encodeURIComponent(
        directory
    )}`;
    const { body } = await onelakeGet(q, token);
    const data = JSON.parse(body || "{}");
    return data.paths || [];
}

async function readBytes(token, lakehouseRelativePath) {
    const p = `/${WORKSPACE}/${lakehouseRelativePath.split("/").map(encodeURIComponent).join("/")}`;
    return onelakeGet(p, token, { raw: true });
}

// ---------------------------------------------------------------------------
// Resolve the lakehouse GUID. Friendly names are disabled on this tenant, so all
// DFS paths must use the bare GUID. Try Fabric REST (needs membership), then fall
// back to discovering it from the workspace filesystem root.
// ---------------------------------------------------------------------------
let lakehouseIdCache = null;

async function resolveLakehouseId() {
    if (lakehouseIdCache) return lakehouseIdCache;

    // 1) Fabric control plane (authoritative; requires workspace membership).
    try {
        const ftoken = await getToken(FABRIC_RESOURCE);
        const out = await fabricGet(`/workspaces/${WORKSPACE}/lakehouses`, ftoken);
        const match = (out.value || []).find(
            (x) => String(x.displayName).toLowerCase() === LAKEHOUSE_NAME.toLowerCase()
        );
        if (match) {
            lakehouseIdCache = match.id;
            return match.id;
        }
    } catch {
        // fall through to data-plane discovery
    }

    // 2) Data-plane discovery: list the workspace root and find the lakehouse item folder.
    const stoken = await getToken(STORAGE_RESOURCE);
    const roots = await listPaths(stoken, "", false);
    // Item folders look like "<guid>" (friendly disabled). Probe each for a Files/corpus child.
    for (const r of roots) {
        const seg = String(r.name).split("/").pop();
        try {
            await listPaths(stoken, `${seg}/Files`, false);
            lakehouseIdCache = seg;
            return seg;
        } catch {}
    }
    throw new CanvasError("lakehouse_unresolved", "Could not resolve the lakehouse GUID.");
}

function fabricGet(path, token) {
    return new Promise((resolve, reject) => {
        const req = httpsRequest(
            {
                host: "api.fabric.microsoft.com",
                path: `/v1${path}`,
                method: "GET",
                headers: { Authorization: `Bearer ${token}` },
            },
            (res) => {
                const chunks = [];
                res.on("data", (c) => chunks.push(c));
                res.on("end", () => {
                    const txt = Buffer.concat(chunks).toString("utf8");
                    if (res.statusCode >= 200 && res.statusCode < 300) {
                        resolve(JSON.parse(txt || "{}"));
                    } else {
                        reject(
                            Object.assign(new Error(`Fabric ${res.statusCode}`), {
                                status: res.statusCode,
                                body: txt.slice(0, 300),
                            })
                        );
                    }
                });
            }
        );
        req.on("error", reject);
        req.end();
    });
}

// ---------------------------------------------------------------------------
// Connection status — distinguishes "az not logged in" / "not a workspace member"
// / "ready", so the iframe can render a precise next step.
// ---------------------------------------------------------------------------
async function getStatus() {
    let signedInAs = null;
    try {
        const acc = JSON.parse(await runAz(["account", "show", "-o", "json"]));
        signedInAs = acc.user?.name || null;
    } catch (e) {
        return {
            ready: false,
            reason: "az_login",
            message: "Azure CLI is not logged in. Run `az login`.",
            workspace: WORKSPACE,
            lakehouse: LAKEHOUSE_NAME,
        };
    }
    try {
        const stoken = await getToken(STORAGE_RESOURCE);
        const lhid = await resolveLakehouseId();
        // Confirm a real read works.
        await listPaths(stoken, `${lhid}/${CORPUS_PREFIX}`, false);
        return {
            ready: true,
            signedInAs,
            workspace: WORKSPACE,
            lakehouse: LAKEHOUSE_NAME,
            lakehouseId: lhid,
            corpusPrefix: CORPUS_PREFIX,
            host: ONELAKE_HOST,
            checkedAt: new Date().toISOString(),
        };
    } catch (e) {
        return {
            ready: false,
            reason: "membership",
            signedInAs,
            workspace: WORKSPACE,
            lakehouse: LAKEHOUSE_NAME,
            message:
                `${signedInAs} cannot read workspace ${WORKSPACE} (${e.code || e.status || e.message}). ` +
                `Add this user as a Member of the Fabric workspace, then refresh.`,
        };
    }
}

// ---------------------------------------------------------------------------
// Files: live recursive listing of Files/corpus
// ---------------------------------------------------------------------------
async function listFiles() {
    const stoken = await getToken(STORAGE_RESOURCE);
    const lhid = await resolveLakehouseId();
    const base = `${lhid}/${CORPUS_PREFIX}`;
    const paths = await listPaths(stoken, base, true);
    const prefix = `${lhid}/`;
    const files = paths
        .map((p) => ({
            path: String(p.name).startsWith(prefix)
                ? String(p.name).slice(prefix.length)
                : String(p.name),
            dir: p.isDirectory === "true" || p.isDirectory === true,
            size: Number(p.contentLength || 0),
            modified: p.lastModified || "",
        }))
        .filter((f) => !f.dir)
        .sort((a, b) => a.path.localeCompare(b.path));
    return { ok: true, prefix: CORPUS_PREFIX, files };
}

const TEXT_EXT = new Set(["md", "txt", "json", "csv", "html", "htm", "xml", "yaml", "yml", "log"]);
function extOf(p) {
    const m = /\.([a-z0-9]+)$/i.exec(p);
    return m ? m[1].toLowerCase() : "";
}
function contentTypeFor(p) {
    const e = extOf(p);
    return (
        {
            html: "text/html; charset=utf-8",
            htm: "text/html; charset=utf-8",
            json: "application/json; charset=utf-8",
            md: "text/plain; charset=utf-8",
            txt: "text/plain; charset=utf-8",
            csv: "text/plain; charset=utf-8",
            xml: "text/plain; charset=utf-8",
            pdf: "application/pdf",
            png: "image/png",
            jpg: "image/jpeg",
            jpeg: "image/jpeg",
            docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }[e] || "application/octet-stream"
    );
}

// ---------------------------------------------------------------------------
// Tables: enumerate Delta tables under Tables/ and read live schema + row counts
// from the Delta transaction log (_delta_log/*.json). Tiny tables, single commit.
// ---------------------------------------------------------------------------
async function listTables() {
    const stoken = await getToken(STORAGE_RESOURCE);
    const lhid = await resolveLakehouseId();
    let tableDirs = [];
    try {
        const entries = await listPaths(stoken, `${lhid}/Tables`, false);
        tableDirs = entries
            .filter((p) => p.isDirectory === "true" || p.isDirectory === true)
            .map((p) => String(p.name).split("/").pop());
    } catch (e) {
        return { ok: true, tables: [], note: `No Tables/ readable (${e.code || e.status || ""})` };
    }
    const tables = [];
    for (const name of tableDirs) {
        try {
            tables.push(await readDeltaMeta(stoken, lhid, name));
        } catch (e) {
            tables.push({ name, rows: null, columns: [], error: e.code || e.message });
        }
    }
    tables.sort((a, b) => a.name.localeCompare(b.name));
    return { ok: true, tables };
}

async function readDeltaMeta(stoken, lhid, name) {
    const logDir = `${lhid}/Tables/${name}/_delta_log`;
    const entries = await listPaths(stoken, logDir, false);
    const commits = entries
        .map((p) => String(p.name).split("/").pop())
        .filter((n) => /^\d+\.json$/.test(n))
        .sort();
    let columns = [];
    let provider = null;
    // Track active data files (add minus remove) and replay commits in order so that
    // overwrites — which `remove` the prior file (often without stats) and `add` a new
    // one — resolve to the *current* row count rather than the sum of every commit.
    const active = new Map(); // file path -> numRecords
    for (const c of commits) {
        const buf = await readBytes(stoken, `${logDir}/${c}`);
        for (const line of buf.toString("utf8").split("\n")) {
            const t = line.trim();
            if (!t) continue;
            let obj;
            try {
                obj = JSON.parse(t);
            } catch {
                continue;
            }
            if (obj.metaData?.schemaString) {
                try {
                    const sch = JSON.parse(obj.metaData.schemaString);
                    columns = (sch.fields || []).map((f) => ({
                        name: f.name,
                        type:
                            typeof f.type === "string"
                                ? f.type
                                : f.type?.type || JSON.stringify(f.type),
                    }));
                    provider = obj.metaData.format?.provider || provider;
                } catch {}
            }
            if (obj.add?.path) {
                let n = 0;
                try {
                    n = Number(JSON.parse(obj.add.stats || "{}").numRecords || 0);
                } catch {}
                active.set(obj.add.path, n);
            }
            if (obj.remove?.path) active.delete(obj.remove.path);
        }
    }
    let rows = 0;
    for (const n of active.values()) rows += n;
    return { name, rows, columns, provider };
}

// ---------------------------------------------------------------------------
// HTTP server (per instance): serves the iframe shell + JSON/raw endpoints.
// ---------------------------------------------------------------------------
function json(res, code, obj) {
    res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(obj));
}

async function startServer() {
    const server = createServer(async (req, res) => {
        try {
            const url = new URL(req.url, "http://127.0.0.1");
            if (url.pathname === "/") {
                res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
                res.end(SHELL_HTML);
                return;
            }
            if (url.pathname === "/api/status") return json(res, 200, await getStatus());
            if (url.pathname === "/api/files") return json(res, 200, await listFiles());
            if (url.pathname === "/api/tables") return json(res, 200, await listTables());
            if (url.pathname === "/api/file") {
                const rel = url.searchParams.get("path") || "";
                if (!rel.startsWith(CORPUS_PREFIX)) {
                    return json(res, 400, { error: "path must be under " + CORPUS_PREFIX });
                }
                const stoken = await getToken(STORAGE_RESOURCE);
                const lhid = await resolveLakehouseId();
                const buf = await readBytes(stoken, `${lhid}/${rel}`);
                res.writeHead(200, {
                    "Content-Type": contentTypeFor(rel),
                    "Content-Length": buf.length,
                });
                res.end(buf);
                return;
            }
            res.writeHead(404);
            res.end("not found");
        } catch (e) {
            json(res, 500, { error: e.message, code: e.code, status: e.status });
        }
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const addr = server.address();
    const port = typeof addr === "object" && addr ? addr.port : 0;
    return { server, url: `http://127.0.0.1:${port}/` };
}

// ---------------------------------------------------------------------------
// Iframe shell — all rendering is client-side against the endpoints above.
// ---------------------------------------------------------------------------
const SHELL_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OneLake Corpus Explorer</title><style>
  :root{
    --bg:#0a0b0d; --surface:#141619; --surface-2:#1b1e24; --surface-3:#22262e;
    --border:rgba(255,255,255,.08); --border-strong:rgba(255,255,255,.13);
    --text:#e7e9ec; --text-muted:#9aa1ab; --text-subtle:#6b7280;
    --accent:#818cf8; --accent-hover:#6366f1; --accent-soft:rgba(129,140,248,.13); --accent-fg:#0a0b0d;
    --success:#34d399; --success-soft:rgba(52,211,153,.13);
    --warning:#fbbf24; --warning-soft:rgba(251,191,36,.12);
    --danger:#f87171;
    --r-sm:6px; --r-md:8px; --r-lg:12px; --r-xl:16px; --r-full:9999px;
    --shadow-xs:0 1px 2px rgba(0,0,0,.4);
    --shadow-sm:0 1px 3px rgba(0,0,0,.5),0 1px 2px rgba(0,0,0,.3);
    --shadow-md:0 6px 16px rgba(0,0,0,.45);
    --ease-out:cubic-bezier(.16,1,.3,1);
    --font:'Inter',var(--font-sans,-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif);
    --mono:var(--font-mono,ui-monospace,SFMono-Regular,Menlo,'Cascadia Code',monospace);
  }
  /* Light palette — driven by the app theme (data-theme resolved from the host's
     data-color-mode / data-visual-mode), with direct attribute + OS fallbacks. */
  :root[data-theme="light"],
  :root[data-color-mode="light"],
  :root[data-visual-mode="light"]{
    --bg:#fafafa; --surface:#ffffff; --surface-2:#f4f4f5; --surface-3:#ececee;
    --border:#e4e4e7; --border-strong:#d4d4d8;
    --text:#18181b; --text-muted:#71717a; --text-subtle:#a1a1aa;
    --accent:#6366f1; --accent-hover:#4f46e5; --accent-soft:#eef2ff; --accent-fg:#ffffff;
    --success:#16a34a; --success-soft:#f0fdf4; --warning:#b45309; --warning-soft:#fffbeb; --danger:#dc2626;
    --shadow-xs:0 1px 2px rgba(0,0,0,.05);
    --shadow-sm:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.04);
    --shadow-md:0 6px 16px rgba(0,0,0,.10);
  }
  @media (prefers-color-scheme:light){:root:not([data-theme]):not([data-color-mode]):not([data-visual-mode]){
    --bg:#fafafa; --surface:#ffffff; --surface-2:#f4f4f5; --surface-3:#ececee;
    --border:#e4e4e7; --border-strong:#d4d4d8;
    --text:#18181b; --text-muted:#71717a; --text-subtle:#a1a1aa;
    --accent:#6366f1; --accent-hover:#4f46e5; --accent-soft:#eef2ff; --accent-fg:#ffffff;
    --success:#16a34a; --success-soft:#f0fdf4; --warning:#b45309; --warning-soft:#fffbeb; --danger:#dc2626;
    --shadow-xs:0 1px 2px rgba(0,0,0,.05);
    --shadow-sm:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.04);
    --shadow-md:0 6px 16px rgba(0,0,0,.10);
  }}
  *{box-sizing:border-box;margin:0;padding:0}
  html{color-scheme:light dark}
  body{font-family:var(--font);background:var(--bg);color:var(--text);font-size:13px;line-height:1.5;-webkit-font-smoothing:antialiased;padding:24px 28px 56px}
  a{color:var(--accent);text-decoration:none}
  ::selection{background:var(--accent-soft)}
  .ic{display:inline-flex;align-items:center;justify-content:center;flex:none}
  .ic svg{width:1em;height:1em;display:block}

  /* Header */
  .top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:20px}
  .brandrow{display:flex;align-items:center;gap:12px}
  .logo{width:36px;height:36px;border-radius:var(--r-md);background:var(--accent-soft);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:19px;border:1px solid var(--border)}
  .brand h1{font-size:16px;font-weight:650;letter-spacing:-.01em;line-height:1.2}
  .brand .sub{color:var(--text-muted);font-size:12px;margin-top:2px;max-width:640px}
  .meta{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:12px}
  .live{display:inline-flex;align-items:center;gap:6px;color:var(--success);font-size:11px;font-weight:600;letter-spacing:.04em;background:var(--success-soft);padding:3px 9px;border-radius:var(--r-full)}
  .live .dot{width:6px;height:6px;border-radius:50%;background:var(--success);box-shadow:0 0 0 0 var(--success);animation:pulse 2s var(--ease-out) infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--success) 55%,transparent)}70%{box-shadow:0 0 0 6px transparent}100%{box-shadow:0 0 0 0 transparent}}
  .chip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;background:var(--surface);border:1px solid var(--border);color:var(--text);padding:3px 9px;border-radius:var(--r-sm);max-width:280px}
  .chip b{color:var(--text-subtle);font-weight:500}
  .chip .v{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  /* Buttons */
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:34px;padding:0 14px;border-radius:var(--r-md);font:500 13px/1 var(--font);border:1px solid var(--border-strong);background:var(--surface);color:var(--text);cursor:pointer;white-space:nowrap;transition:background .15s,border-color .15s,transform .05s var(--ease-out)}
  .btn:hover{background:var(--surface-2)}
  .btn:active{transform:translateY(.5px)}
  .btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .btn .ic{font-size:15px}
  .btn--primary{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
  .btn--primary:hover{background:var(--accent-hover);border-color:var(--accent-hover)}
  .btn--ghost{height:30px;padding:0 10px;background:transparent;border-color:transparent;color:var(--text-muted);font-size:12px}
  .btn--ghost:hover{background:var(--surface-2);color:var(--text)}
  .btn.spin .ic{animation:rot 1s linear infinite}
  @keyframes rot{to{transform:rotate(360deg)}}

  /* Segmented tabs */
  .seg{display:inline-flex;gap:3px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-lg);padding:3px;margin-bottom:18px}
  .seg-btn{display:inline-flex;align-items:center;gap:7px;height:30px;padding:0 12px;border:0;background:transparent;color:var(--text-muted);font:500 13px/1 var(--font);border-radius:var(--r-md);cursor:pointer;transition:background .15s,color .15s}
  .seg-btn .ic{font-size:15px}
  .seg-btn:hover{color:var(--text)}
  .seg-btn[aria-selected="true"]{background:var(--surface);color:var(--text);box-shadow:var(--shadow-xs)}
  .seg-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .cnt{font-variant-numeric:tabular-nums;font-size:11px;font-weight:600;color:var(--text-subtle);background:var(--surface-3);padding:1px 6px;border-radius:var(--r-full);min-width:18px;text-align:center}
  .seg-btn[aria-selected="true"] .cnt{color:var(--accent);background:var(--accent-soft)}

  /* Layout */
  .layout{display:grid;grid-template-columns:300px 1fr;gap:16px;align-items:start}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden}
  .panel-head{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--border);min-height:46px}
  .panel-head .pt{font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--text-muted)}
  .panel-head .summary{font-size:11px;color:var(--text-subtle);font-variant-numeric:tabular-nums}

  /* Tree */
  .tree{padding:6px;max-height:74vh;overflow:auto}
  .grp{margin-bottom:2px}
  .grp>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:var(--r-sm);font-size:12px;font-weight:600;color:var(--text);transition:background .12s}
  .grp>summary::-webkit-details-marker{display:none}
  .grp>summary:hover{background:var(--surface-2)}
  .grp>summary .chev{font-size:14px;color:var(--text-subtle);transition:transform .15s var(--ease-out)}
  .grp[open]>summary .chev{transform:rotate(90deg)}
  .grp>summary .fold{font-size:15px;color:var(--text-muted)}
  .grp>summary .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .grp>summary .cnt{font-weight:600}
  .file{display:flex;align-items:center;gap:8px;padding:6px 8px 6px 26px;border-radius:var(--r-sm);cursor:pointer;color:var(--text-muted);transition:background .12s,color .12s;border-left:2px solid transparent}
  .file:hover{background:var(--surface-2);color:var(--text)}
  .file:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
  .file.sel{background:var(--accent-soft);color:var(--text);border-left-color:var(--accent)}
  .file.sel .fic{color:var(--accent)}
  .file .fic{font-size:14px;color:var(--text-subtle);flex:none}
  .file .fn{flex:1;font-family:var(--mono);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .file .fs{font-size:11px;color:var(--text-subtle);font-variant-numeric:tabular-nums;white-space:nowrap}

  /* Preview */
  .view{min-height:60vh;display:flex;flex-direction:column}
  .vp{display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:12px;color:var(--text);overflow:hidden}
  .vp .fic{color:var(--accent);font-size:14px;flex:none}
  .vp .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .vbody{flex:1;overflow:auto}
  pre.doc{margin:0;padding:18px 20px;font-family:var(--mono);font-size:12px;line-height:1.65;white-space:pre-wrap;word-break:break-word;color:var(--text)}
  iframe.doc,embed.doc{width:100%;height:74vh;border:0;background:#fff}
  img.doc{max-width:100%;display:block;margin:20px auto;border-radius:var(--r-sm)}

  /* Empty / placeholder */
  .empty{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:10px;padding:64px 24px;color:var(--text-subtle)}
  .empty .eic{width:44px;height:44px;border-radius:var(--r-lg);background:var(--surface-2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:20px;color:var(--text-muted)}
  .empty p{font-size:13px;max-width:280px}

  /* Tables */
  .tbl-summary{display:flex;align-items:center;gap:10px;margin-bottom:12px;font-size:12px;color:var(--text-muted)}
  .tbl-summary b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
  .grid{width:100%;border-collapse:collapse;font-size:13px}
  .grid th{text-align:left;color:var(--text-subtle);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:11px 16px;border-bottom:1px solid var(--border);background:var(--surface-2);position:sticky;top:0;z-index:1}
  .grid td{padding:13px 16px;border-bottom:1px solid var(--border);vertical-align:top}
  .grid tbody tr{transition:background .12s}
  .grid tbody tr:last-child td{border-bottom:0}
  .grid tbody tr:hover{background:var(--surface-2)}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  .num.rows{color:var(--text);font-weight:600}
  .tname{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:12.5px;color:var(--text)}
  .tname .fic{color:var(--accent);font-size:15px}
  .cols{display:flex;flex-wrap:wrap;gap:5px;max-width:520px}
  .col{font-size:11px;font-family:var(--mono);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-sm);padding:2px 7px;color:var(--text)}
  .col i{color:var(--text-subtle);font-style:normal;margin-left:4px}
  .col.err{color:var(--danger);background:transparent;border-color:transparent;padding-left:0}

  /* Gate */
  .gate{max-width:560px;margin:48px auto;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-xl);padding:32px;box-shadow:var(--shadow-md)}
  .gate .gic{width:44px;height:44px;border-radius:var(--r-lg);background:var(--warning-soft);color:var(--warning);display:flex;align-items:center;justify-content:center;font-size:21px;margin-bottom:16px}
  .gate h2{font-size:17px;font-weight:650;letter-spacing:-.01em}
  .gate .callout{margin-top:8px;background:var(--warning-soft);border:1px solid color-mix(in srgb,var(--warning) 25%,transparent);color:var(--text);border-radius:var(--r-md);padding:10px 12px;font-size:12.5px}
  .gate .lead{margin-top:18px;color:var(--text-muted);font-size:13px}
  .gate ol{margin:12px 0 0;padding-left:20px;color:var(--text);font-size:13px;line-height:1.9}
  .gate code{background:var(--surface-2);border:1px solid var(--border);padding:1px 6px;border-radius:var(--r-sm);font-family:var(--mono);font-size:12px}
  .gate .actions{margin-top:24px;display:flex;gap:8px}

  /* Skeletons */
  .skel{position:relative;overflow:hidden;background:var(--surface-2);border-radius:var(--r-sm)}
  .skel::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--text) 7%,transparent),transparent);transform:translateX(-100%);animation:shimmer 1.4s var(--ease-out) infinite}
  @keyframes shimmer{to{transform:translateX(100%)}}
  .skel-row{display:flex;align-items:center;gap:8px;padding:7px 8px}
  .skel-lines{padding:18px 20px;display:flex;flex-direction:column;gap:10px}

  /* Boot */
  .boot{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;min-height:60vh;color:var(--text-muted)}
  .boot .logo{width:48px;height:48px;font-size:24px}
  .boot .spinner{width:18px;height:18px;border:2px solid var(--border-strong);border-top-color:var(--accent);border-radius:50%;animation:rot .7s linear infinite}

  @media (prefers-reduced-motion:reduce){*,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
  @media (max-width:720px){.layout{grid-template-columns:1fr}.tree{max-height:38vh}}
</style>
<script>
// Resolve the app theme (host sets data-color-mode / data-visual-mode on the
// document) into a single data-theme on :root, before first paint to avoid a flash.
(function(){
  function resolveTheme(){
    var el=document.documentElement, b=document.body;
    function read(n){ return (el&&el.getAttribute&&el.getAttribute(n))||(b&&b.getAttribute&&b.getAttribute(n))||''; }
    var m=read('data-visual-mode')||read('data-color-mode');
    var mode = (m==='light'||m==='dark') ? m
      : (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    if(el.getAttribute('data-theme')!==mode) el.setAttribute('data-theme', mode);
  }
  window.__resolveTheme=resolveTheme;
  resolveTheme();
})();
</script>
</head>
<body>
  <div id="app"><div class="boot"><div class="logo" id="bootlogo"></div><div class="spinner"></div><div>Connecting to OneLake…</div></div></div>
<script>
// ---- SVG icon set (Lucide-style, monochrome) ----
function svg(p){return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+p+'</svg>';}
const ICON={
  database:svg('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>'),
  refresh:svg('<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/>'),
  external:svg('<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'),
  folder:svg('<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'),
  chevron:svg('<path d="m9 18 6-6-6-6"/>'),
  fileText:svg('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>'),
  fileCode:svg('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/><path d="m10 12-2 2 2 2"/><path d="m14 16 2-2-2-2"/>'),
  fileImage:svg('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/><circle cx="10" cy="12.5" r="1.5"/><path d="m20 17-2.8-2.8L11 21"/>'),
  file:svg('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/>'),
  table:svg('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M12 3v18"/>'),
  lock:svg('<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'),
  alert:svg('<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>'),
  search:svg('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
};
function ic(name,cls){return '<span class="ic '+(cls||'')+'">'+(ICON[name]||ICON.file)+'</span>';}
function fileIcon(name){const e=(name.split('.').pop()||'').toLowerCase();
  if(['md','txt','log','rst'].includes(e))return 'fileText';
  if(['json','yaml','yml','xml','csv','html','htm','js','ts'].includes(e))return 'fileCode';
  if(['png','jpg','jpeg','gif','svg','webp'].includes(e))return 'fileImage';
  return 'file';}

const fmtBytes=(n)=>{n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(2)+' MB';};
const esc=(v)=>String(v==null?'':v).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num=(n)=>Number(n||0).toLocaleString('en-US');
let STATUS=null, TAB='files', FILES=[], SELECTED=null, TABLES=null, BUSY=false;

document.getElementById('bootlogo').innerHTML=ic('database');

// Keep the canvas theme in sync with live app theme toggles.
(function(){
  var run=function(){ if(window.__resolveTheme) window.__resolveTheme(); };
  run();
  try{
    var mo=new MutationObserver(run);
    mo.observe(document.documentElement,{attributes:true,attributeFilter:['data-color-mode','data-visual-mode','data-light-theme','data-dark-theme']});
    if(document.body) mo.observe(document.body,{attributes:true,attributeFilter:['data-color-mode','data-visual-mode']});
  }catch(e){}
  try{ matchMedia('(prefers-color-scheme: light)').addEventListener('change',run); }catch(e){}
})();

// ---- Event delegation ----
document.addEventListener('click',(e)=>{
  const t=e.target.closest('[data-act]'); if(!t) return;
  const a=t.dataset.act;
  if(a==='tab') setTab(t.dataset.tab);
  else if(a==='refresh') refresh();
  else if(a==='retry') boot();
  else if(a==='file') selectFile(t.dataset.p);
});
document.addEventListener('keydown',(e)=>{
  if(e.key!=='Enter'&&e.key!==' ') return;
  const t=e.target.closest('.file'); if(!t) return;
  e.preventDefault(); selectFile(t.dataset.p);
});

async function boot(){
  try{ STATUS=await (await fetch('/api/status')).json(); }
  catch(e){ STATUS={ready:false,reason:'error',message:String(e)}; }
  if(!STATUS.ready){ renderGate(); return; }
  render(); loadFiles();
}

function renderGate(){
  const s=STATUS||{};
  const member=s.reason==='membership';
  const steps = member
    ? '<div class="lead">To read this lakehouse live, add the signed-in user as a <b>Member</b> of the Fabric workspace:</div>'+
      '<ol><li>Fabric portal → open workspace <code>'+esc(s.workspace)+'</code></li>'+
      '<li><b>Manage access</b> → <b>Add people</b></li>'+
      '<li>Add <code>'+esc(s.signedInAs||'your user')+'</code> with role <b>Member</b></li>'+
      '<li>Return here and click <b>Retry</b></li></ol>'
    : '<div class="lead">Run <code>az login</code> in a terminal, then click <b>Retry</b>.</div>';
  document.getElementById('app').innerHTML=
    header(false)+
    '<div class="gate"><div class="gic">'+ic(member?'lock':'alert')+'</div>'+
    '<h2>'+(member?'Workspace access needed':'Azure sign-in needed')+'</h2>'+
    '<div class="callout">'+esc(s.message||'Not connected to OneLake.')+'</div>'+
    steps+
    '<div class="actions"><button class="btn btn--primary" data-act="retry">'+ic('refresh')+'Retry connection</button></div>'+
    '</div>';
}

function header(withTabs){
  const s=STATUS||{};
  const chips = withTabs
    ? '<div class="meta">'+
        '<span class="live"><span class="dot"></span>LIVE</span>'+
        '<span class="chip"><b>workspace</b><span class="v">'+esc(s.workspace)+'</span></span>'+
        '<span class="chip"><b>lakehouse</b><span class="v">'+esc(s.lakehouse)+'</span></span>'+
        '<span class="chip" title="'+esc(s.lakehouseId)+'"><b>id</b><span class="v">'+esc(s.lakehouseId)+'</span></span>'+
        (s.signedInAs?'<span class="chip"><b>as</b><span class="v">'+esc(s.signedInAs)+'</span></span>':'')+
      '</div>'
    : '';
  return '<div class="top"><div>'+
    '<div class="brandrow"><div class="logo">'+ic('database')+'</div>'+
    '<div class="brand"><h1>OneLake Corpus Explorer</h1>'+
    '<div class="sub">Live document tree and Delta tables read straight from the Microsoft Fabric lakehouse the keystone deploy pushed.</div></div></div>'+
    chips+'</div>'+
    (withTabs?'<button class="btn'+(BUSY?' spin':'')+'" data-act="refresh">'+ic('refresh')+'Refresh</button>':
              '<button class="btn" data-act="retry">'+ic('refresh')+'Retry</button>')+
    '</div>';
}

function tabs(){
  return '<div class="seg" role="tablist">'+
    '<button class="seg-btn" role="tab" aria-selected="'+(TAB==='files')+'" data-act="tab" data-tab="files">'+ic('folder')+'Files <span class="cnt">'+(FILES.length?num(FILES.length):'·')+'</span></button>'+
    '<button class="seg-btn" role="tab" aria-selected="'+(TAB==='tables')+'" data-act="tab" data-tab="tables">'+ic('table')+'Tables <span class="cnt">'+(TABLES?num(TABLES.length):'·')+'</span></button>'+
    '</div>';
}

function render(){
  const body = TAB==='files' ? filesView() : tablesView();
  document.getElementById('app').innerHTML = header(true)+tabs()+body;
}
function setTab(t){ if(TAB===t)return; TAB=t; render(); if(t==='files'&&!FILES.length) loadFiles(); if(t==='tables'&&!TABLES) loadTables(); }
function refresh(){ BUSY=true; FILES=[]; TABLES=null; SELECTED=null; boot(); }

// ---- Files ----
function skelTree(){let s='';for(let i=0;i<7;i++){s+='<div class="skel-row"><span class="skel" style="width:16px;height:16px;border-radius:4px"></span><span class="skel" style="height:11px;flex:1;max-width:'+(120+((i*37)%90))+'px"></span></div>';}return s;}
function filesView(){
  const totalSize=FILES.reduce((a,f)=>a+Number(f.size||0),0);
  let tree, summary;
  if(!FILES.length){ tree=skelTree(); summary='loading…'; }
  else{
    summary=num(FILES.length)+' files · '+fmtBytes(totalSize);
    const groups={};
    for(const f of FILES){ const top=f.path.split('/').slice(0,-1).join('/'); (groups[top]=groups[top]||[]).push(f); }
    tree='';
    for(const g of Object.keys(groups).sort()){
      const label=g.split('/').pop()||g;
      tree+='<details class="grp" open><summary>'+ic('chevron','chev')+ic('folder','fold')+'<span class="nm" title="'+esc(g)+'">'+esc(label)+'</span><span class="cnt">'+groups[g].length+'</span></summary>';
      for(const f of groups[g]){
        const nm=f.path.split('/').pop();
        tree+='<div class="file'+(SELECTED===f.path?' sel':'')+'" tabindex="0" role="button" data-act="file" data-p="'+esc(f.path)+'">'+
          ic(fileIcon(nm),'fic')+'<span class="fn">'+esc(nm)+'</span><span class="fs">'+fmtBytes(f.size)+'</span></div>';
      }
      tree+='</details>';
    }
  }
  return '<div class="layout">'+
    '<div class="panel"><div class="panel-head"><span class="pt">Documents</span><span class="summary">'+esc(summary)+'</span></div><div class="tree">'+tree+'</div></div>'+
    '<div class="panel view" id="view">'+previewHtml()+'</div></div>';
}
function previewHtml(){
  if(!SELECTED) return '<div class="panel-head"><span class="pt">Preview</span></div>'+
    '<div class="empty"><div class="eic">'+ic('search')+'</div><p>Select a document to preview its live content from OneLake.</p></div>';
  const name=SELECTED.split('/').pop();
  const ext=(name.split('.').pop()||'').toLowerCase();
  const src='/api/file?path='+encodeURIComponent(SELECTED);
  const head='<div class="panel-head"><span class="vp">'+ic(fileIcon(name),'fic')+'<span class="t">'+esc(SELECTED)+'</span></span>'+
    '<a class="btn btn--ghost" href="'+src+'" target="_blank" rel="noopener">'+ic('external')+'Raw</a></div>';
  const TEXT=['md','txt','json','csv','xml','yaml','yml','log','rst'];
  let body;
  if(TEXT.includes(ext)){
    body='<div class="vbody" id="vbody"><div class="skel-lines">'+
      Array.from({length:7}).map((_,i)=>'<span class="skel" style="height:12px;width:'+(50+((i*53)%45))+'%"></span>').join('')+'</div></div>';
    setTimeout(()=>{ fetch(src).then(r=>r.text()).then(txt=>{ const b=document.getElementById('vbody'); if(b){ const pre=document.createElement('pre'); pre.className='doc'; pre.textContent=txt; b.innerHTML=''; b.appendChild(pre);} }).catch(()=>{}); },0);
  } else if(['html','htm'].includes(ext)){
    body='<div class="vbody"><iframe class="doc" sandbox="" src="'+src+'"></iframe></div>';
  } else if(ext==='pdf'){
    body='<div class="vbody"><embed class="doc" type="application/pdf" src="'+src+'"></div>';
  } else if(['png','jpg','jpeg','gif','svg','webp'].includes(ext)){
    body='<div class="vbody"><img class="doc" src="'+src+'" alt="'+esc(name)+'"></div>';
  } else {
    body='<div class="empty"><div class="eic">'+ic('file')+'</div><p>Binary file (.'+esc(ext)+'). Open it raw to download.</p><a class="btn" href="'+src+'" target="_blank" rel="noopener">'+ic('external')+'Open raw</a></div>';
  }
  return head+body;
}
function selectFile(p){
  SELECTED=p;
  const v=document.getElementById('view'); if(v) v.innerHTML=previewHtml();
  document.querySelectorAll('.file').forEach(e=>{const on=e.dataset.p===p;e.classList.toggle('sel',on);if(on)e.scrollIntoView({block:'nearest'});});
}
async function loadFiles(){
  try{ const r=await (await fetch('/api/files')).json(); FILES=r.files||[]; BUSY=false; if(TAB==='files') render(); }
  catch(e){ BUSY=false; document.getElementById('app').innerHTML=header(true)+tabs()+'<div class="panel"><div class="empty"><div class="eic">'+ic('alert')+'</div><p>Failed to list files: '+esc(e.message)+'</p></div></div>'; }
}

// ---- Tables ----
function skelTableRows(){let s='';for(let i=0;i<5;i++){s+='<tr><td><div class="skel" style="height:14px;width:160px"></div></td><td class="num"><div class="skel" style="height:14px;width:40px;margin-left:auto"></div></td><td class="num"><div class="skel" style="height:14px;width:24px;margin-left:auto"></div></td><td><div class="skel" style="height:14px;width:80%"></div></td></tr>';}return s;}
function tablesView(){
  let summary, rows;
  if(!TABLES){ summary=''; rows=skelTableRows(); }
  else if(!TABLES.length){
    return '<div class="panel"><div class="empty"><div class="eic">'+ic('table')+'</div><p>No Delta tables found under Tables/.</p></div></div>';
  } else {
    const totalRows=TABLES.reduce((a,t)=>a+(t.rows==null?0:Number(t.rows)),0);
    summary='<div class="tbl-summary">'+ic('table')+'<span><b>'+num(TABLES.length)+'</b> tables · <b>'+num(totalRows)+'</b> rows</span></div>';
    rows='';
    for(const t of TABLES){
      const cols=(t.columns||[]).map(c=>'<span class="col">'+esc(c.name)+'<i>'+esc(c.type)+'</i></span>').join('');
      rows+='<tr><td><span class="tname">'+ic('table','fic')+esc(t.name)+'</span></td>'+
        '<td class="num rows">'+(t.rows==null?'—':num(t.rows))+'</td>'+
        '<td class="num">'+(t.columns?t.columns.length:0)+'</td>'+
        '<td><div class="cols">'+(cols||'<span class="col err">'+esc(t.error||'no schema')+'</span>')+'</div></td></tr>';
    }
  }
  return (summary||'')+'<div class="panel"><table class="grid"><thead><tr><th>Delta table</th><th class="num">Rows</th><th class="num">Cols</th><th>Schema</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}
async function loadTables(){
  try{ const r=await (await fetch('/api/tables')).json(); TABLES=r.tables||[]; if(TAB==='tables') render(); }
  catch(e){ TABLES=[]; if(TAB==='tables') document.getElementById('app').innerHTML=header(true)+tabs()+'<div class="panel"><div class="empty"><div class="eic">'+ic('alert')+'</div><p>Failed to read tables: '+esc(e.message)+'</p></div></div>'; }
}

boot();
</script>
</body></html>`;

// ---------------------------------------------------------------------------
// Canvas declaration
// ---------------------------------------------------------------------------
const session = await joinSession({
    canvases: [
        createCanvas({
            id: "onelake-corpus-explorer",
            displayName: "OneLake Corpus Explorer",
            description:
                "Live browser for the Waypoint demo corpus in a Microsoft Fabric OneLake lakehouse (pushed by the keystone one-click deploy). Reads the OneLake DFS endpoint directly: the Files/ document tree with content preview and the Tables/ Delta tables with live schema + row counts. Requires the signed-in az user to be a workspace Member.",
            actions: [
                {
                    name: "status",
                    description:
                        "Check the live OneLake connection: whether az is logged in and the user can read the Fabric workspace/lakehouse.",
                    handler: async () => getStatus(),
                },
                {
                    name: "list_files",
                    description: "List the live Files/corpus document tree from OneLake.",
                    handler: async () => listFiles(),
                },
                {
                    name: "list_tables",
                    description:
                        "List the live Delta tables under Tables/ with schema and row counts read from the Delta transaction log.",
                    handler: async () => listTables(),
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer();
                    servers.set(ctx.instanceId, entry);
                }
                let title = "⬢ OneLake Corpus Explorer";
                try {
                    const s = await getStatus();
                    title = s.ready
                        ? `⬢ OneLake corpus · ${s.lakehouse} (live)`
                        : "⬢ OneLake Corpus — access needed";
                } catch {}
                return { title, url: entry.url };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});
