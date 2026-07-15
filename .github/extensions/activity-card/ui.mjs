// UI + client-side Adaptive Card renderer for the activity-card canvas.
//
// Dependency-free renderer covering the element vocabulary that
// agents/invoice-analyst/adaptive_cards.py emits for the decision-ready
// digest card: TextBlock, RichTextBlock, FactSet, Table, Container (with
// style + selectAction), ColumnSet/Column, and card-level Action.OpenUrl.
// Unknown element types degrade to a JSON dump. Styled to approximate how
// Teams renders the card so this is a faithful *preview* of the AI Teammate
// surface.

export function renderHtml(instanceId, defaultEndpoint) {
    const endpoint = String(defaultEndpoint || "http://127.0.0.1:8090/api/messages");
    return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Adaptive Card preview</title>
<style>
  :root {
    color-scheme: light dark;
    --purple: #5b5fc7;
    --purple-d: #4f52b2;
    --risk: #c4314b;
    --amber: #b26a00;
    --ok: #0f7b0f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--background-color-default, #f5f5f5);
    color: var(--text-color-default, #242424);
    font-family: var(--font-sans, "Segoe UI", system-ui, -apple-system, sans-serif);
    font-size: var(--text-body-medium, 14px);
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
  }
  header {
    padding: 14px 18px;
    border-bottom: 1px solid var(--border-color-default, #e6e6e6);
    display: flex; align-items: center; gap: 10px;
  }
  .avatar {
    width: 30px; height: 30px; border-radius: 50%; background: var(--purple);
    color: #fff; font-weight: 700; display: flex; align-items: center;
    justify-content: center; font-size: 14px; flex: 0 0 auto;
  }
  header h1 { margin: 0; font-size: 15px; font-weight: 700; }
  header .sub { color: var(--text-color-muted, #8a8a8a); font-size: 12px; }
  .wrap { padding: 16px 18px; max-width: 640px; margin: 0 auto; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
  .chip {
    padding: 5px 10px; font-size: 12px; border-radius: 999px;
    background: transparent; color: var(--text-color-default, #242424);
    border: 1px solid var(--border-color-default, #e6e6e6); font-weight: 500; cursor: pointer;
  }
  .chip:hover { border-color: var(--purple); color: var(--purple-d); }
  .row { display: flex; gap: 8px; margin-bottom: 10px; }
  .row input[type=text] {
    flex: 1; padding: 9px 11px;
    border: 1px solid var(--border-color-default, #e6e6e6); border-radius: 8px;
    background: var(--background-color-default, #fff); color: inherit; font: inherit;
  }
  #endpoint { width: 100%; margin-bottom: 10px; padding: 8px 10px;
    border: 1px solid var(--border-color-default, #e6e6e6); border-radius: 6px;
    background: var(--background-color-default, #fff); color: inherit; font: inherit; }
  button {
    padding: 9px 16px; border: 1px solid var(--purple); border-radius: 6px;
    background: var(--purple); color: #fff; font: inherit; font-weight: 600; cursor: pointer;
  }
  button:disabled { opacity: .55; cursor: default; }
  .status { font-size: 12px; color: var(--text-color-muted, #8a8a8a); min-height: 16px; margin-bottom: 12px; }
  .status.err { color: var(--risk); }

  /* ---- Adaptive Card surface (Teams-like) ---- */
  .ac-card {
    background: var(--background-color-default, #fff);
    border: 1px solid var(--border-color-default, #e6e6e6);
    border-radius: 10px;
    padding: 16px 16px 14px;
    box-shadow: 0 1px 2px rgba(0,0,0,.10), 0 2px 10px rgba(0,0,0,.05);
    max-width: 460px;
  }
  .ac-block { margin-top: 8px; }
  .ac-block:first-child { margin-top: 0; }
  .sp-None { margin-top: 0; }
  .sp-Small { margin-top: 6px; }
  .sp-Medium { margin-top: 14px; }

  .ac-tb { white-space: pre-wrap; word-break: break-word; }
  .w-Bolder { font-weight: 700; }
  .s-Large { font-size: 17px; }
  .s-Medium { font-size: 14.5px; }
  .s-Small { font-size: 11.5px; }
  .subtle { color: var(--text-color-muted, #8a8a8a); }
  .align-Right { text-align: right; }
  .c-Attention { color: var(--risk); }
  .c-Good { color: var(--ok); }
  .c-Warning { color: var(--amber); }
  .c-Accent { color: var(--purple-d); font-weight: 600; }
  .eyebrow { letter-spacing: .04em; text-transform: uppercase; }

  .ac-rich { white-space: pre-wrap; }

  .ac-factset { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
  .ac-fact-t { font-weight: 600; color: var(--text-color-muted, #8a8a8a); }

  table.ac-table { border-collapse: collapse; width: 100%; font-size: 13px; }
  table.ac-table td { border: 1px solid var(--border-color-default, #e6e6e6); padding: 6px 10px; vertical-align: top; }
  table.ac-table tr.ac-head td { font-weight: 700; background: color-mix(in srgb, var(--text-color-default, #242424) 6%, transparent); }

  /* Container styles */
  .ac-container { padding: 9px 11px; border-radius: 8px; }
  .ac-container.has-style { border: 1px solid var(--border-color-default, #e6e6e6); }
  .st-emphasis { background: color-mix(in srgb, var(--text-color-default, #242424) 4%, transparent); }
  .st-good { background: color-mix(in srgb, var(--ok) 12%, transparent); border-color: color-mix(in srgb, var(--ok) 35%, transparent) !important; }
  .st-attention { background: color-mix(in srgb, var(--risk) 10%, transparent); border-color: color-mix(in srgb, var(--risk) 35%, transparent) !important; }
  .st-warning { background: color-mix(in srgb, var(--amber) 12%, transparent); }
  .st-accent { background: color-mix(in srgb, var(--purple) 12%, transparent); }
  .ac-container.clickable { cursor: pointer; }
  .ac-container.clickable:hover { border-color: var(--purple) !important; }
  .ac-container.sep { border-top: 1px solid var(--border-color-default, #e6e6e6); border-radius: 0; padding: 8px 0 0; }

  .ac-cols { display: flex; gap: 10px; align-items: flex-start; }
  .ac-col { min-width: 0; }
  .ac-col.w-stretch { flex: 1 1 auto; }
  .ac-col.w-auto { flex: 0 0 auto; }
  .ac-col.vc-Center { align-self: center; }

  .ac-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .ac-actions a {
    text-decoration: none; font-size: 12.5px; font-weight: 600;
    padding: 8px 13px; border-radius: 6px; border: 1px solid var(--purple);
    background: var(--purple); color: #fff;
  }
  .ac-actions a.positive { background: var(--purple); border-color: var(--purple); }

  .meta { margin-top: 14px; }
  .meta summary { cursor: pointer; color: var(--text-color-muted, #8a8a8a); font-size: 12px; }
  .meta pre { margin: 8px 0 0; padding: 10px; overflow: auto; background: color-mix(in srgb, var(--text-color-default, #242424) 5%, transparent); border-radius: 6px; font-family: var(--font-mono, monospace); font-size: 12px; }
  .fallback { border-left: 3px solid var(--risk); padding: 8px 12px; margin-top: 10px; white-space: pre-wrap; background: color-mix(in srgb, var(--risk) 8%, transparent); border-radius: 4px; font-size: 13px; }
  .tag { display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 4px; margin-bottom: 8px; }
  .tag.card { background: color-mix(in srgb, var(--purple) 15%, transparent); color: var(--purple-d); }
  .tag.text { background: color-mix(in srgb, var(--risk) 15%, transparent); color: var(--risk); }
</style>
</head>
<body>
<header>
  <div class="avatar">A</div>
  <div>
    <h1>Adaptive Card preview &mdash; Invoice Analyst</h1>
    <div class="sub">Sends over the activity protocol (<code>/api/messages</code>) through the local Foundry bridge and renders the card the agent replies with.</div>
  </div>
</header>
<div class="wrap">
  <input id="endpoint" type="text" value="${endpoint}" spellcheck="false" />
  <div class="chips">
    <span class="chip" data-p="What is the status of our runs?">Status of runs</span>
    <span class="chip" data-p="What invoices need my decision today?">Invoices needing a decision</span>
    <span class="chip" data-p="Summarize the active investigation queue by severity.">Queue by severity</span>
  </div>
  <div class="row">
    <input id="msg" type="text" placeholder="Ask the assurance analyst…" value="What invoices need my decision today?" />
    <button id="send">Send</button>
  </div>
  <div id="status" class="status"></div>
  <div id="out"></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const outEl = $("out");

function setStatus(t, err) { statusEl.textContent = t || ""; statusEl.className = "status" + (err ? " err" : ""); }

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function tbClasses(b, extra) {
  let c = "ac-tb " + (extra || "");
  if (b.weight === "Bolder") c += " w-Bolder";
  if (b.size) c += " s-" + b.size;
  if (b.isSubtle) c += " subtle";
  if (b.color && b.color !== "Default") c += " c-" + b.color;
  if (b.horizontalAlignment === "Right") c += " align-Right";
  return c.trim();
}

function renderTextBlock(b, extraCls) {
  const n = el("div", "ac-block " + tbClasses(b, extraCls) + " sp-" + (b.spacing || "Default"));
  n.appendChild(document.createTextNode(String(b.text ?? "")));
  return n;
}

function renderRichText(b) {
  const n = el("div", "ac-block ac-rich sp-" + (b.spacing || "Default"));
  for (const run of b.inlines || []) {
    if (typeof run === "string") { n.appendChild(document.createTextNode(run)); continue; }
    const span = el("span", tbClasses(run), String(run.text ?? ""));
    n.appendChild(span);
  }
  return n;
}

function renderFactSet(b) {
  const wrap = el("div", "ac-block sp-" + (b.spacing || "Default"));
  const grid = el("div", "ac-factset");
  for (const f of b.facts || []) {
    grid.appendChild(el("div", "ac-fact-t", String(f.title ?? "")));
    grid.appendChild(el("div", "", String(f.value ?? "")));
  }
  wrap.appendChild(grid);
  return wrap;
}

function cellText(cell) {
  const items = cell && cell.items ? cell.items : [];
  return items.map((i) => String(i.text ?? "")).join(" ");
}

function renderTable(b) {
  const wrap = el("div", "ac-block sp-" + (b.spacing || "Default"));
  const table = el("table", "ac-table");
  (b.rows || []).forEach((r, idx) => {
    const tr = el("tr", idx === 0 && b.firstRowAsHeaders ? "ac-head" : "");
    for (const cell of r.cells || []) tr.appendChild(el("td", "", cellText(cell)));
    table.appendChild(tr);
  });
  wrap.appendChild(table);
  return wrap;
}

function renderColumnSet(b) {
  const wrap = el("div", "ac-block ac-cols sp-" + (b.spacing || "Default"));
  for (const col of b.columns || []) {
    let w = col.width;
    const cls = "ac-col " + (w === "stretch" || w == null ? "w-stretch" : w === "auto" ? "w-auto" : "w-stretch") +
      (col.verticalContentAlignment ? " vc-" + col.verticalContentAlignment : "");
    const c = el("div", cls);
    for (const item of col.items || []) c.appendChild(renderElement(item));
    wrap.appendChild(c);
  }
  return wrap;
}

function renderContainer(b) {
  let cls = "ac-block ac-container sp-" + (b.spacing || "Default");
  if (b.style) cls += " has-style st-" + b.style;
  if (b.separator) cls += " sep";
  const url = b.selectAction && b.selectAction.type === "Action.OpenUrl" ? b.selectAction.url : null;
  if (url) cls += " clickable";
  const n = el("div", cls);
  for (const item of b.items || []) n.appendChild(renderElement(item));
  if (url) {
    n.setAttribute("role", "link");
    n.title = url;
    n.addEventListener("click", () => window.open(url, "_blank", "noopener"));
  }
  return n;
}

function renderElement(b) {
  switch (b && b.type) {
    case "TextBlock": return renderTextBlock(b, b.__eyebrow ? "eyebrow" : "");
    case "RichTextBlock": return renderRichText(b);
    case "FactSet": return renderFactSet(b);
    case "Table": return renderTable(b);
    case "ColumnSet": return renderColumnSet(b);
    case "Container": return renderContainer(b);
    default: {
      const n = el("div", "ac-block");
      const pre = el("pre"); pre.textContent = JSON.stringify(b, null, 2);
      n.appendChild(pre); return n;
    }
  }
}

function renderCard(card) {
  const surface = el("div", "ac-card");
  const body = card.body || [];
  // Tag the first small-bold TextBlock as the uppercase eyebrow for styling.
  if (body[0] && body[0].type === "TextBlock" && body[0].size === "Small" && body[0].weight === "Bolder") {
    body[0].__eyebrow = true;
  }
  for (const b of body) surface.appendChild(renderElement(b));
  const actions = card.actions || [];
  if (actions.length) {
    const bar = el("div", "ac-actions");
    for (const a of actions) {
      if (a.type !== "Action.OpenUrl" || !a.url) continue;
      const link = el("a", a.style === "positive" ? "positive" : "", a.title || "Open");
      link.href = a.url; link.target = "_blank"; link.rel = "noopener";
      bar.appendChild(link);
    }
    surface.appendChild(bar);
  }
  return surface;
}

function renderReply(reply) {
  outEl.innerHTML = "";
  const atts = reply.attachments || [];
  const card = atts.find((a) => a && a.contentType === "application/vnd.microsoft.card.adaptive" && a.content);
  if (card) {
    outEl.appendChild(el("span", "tag card", "Adaptive Card attachment"));
    outEl.appendChild(renderCard(card.content));
    const meta = el("details", "meta");
    meta.appendChild(el("summary", "", "Raw card JSON"));
    const pre = el("pre"); pre.textContent = JSON.stringify(card.content, null, 2);
    meta.appendChild(pre); outEl.appendChild(meta);
  } else {
    outEl.appendChild(el("span", "tag text", "Plain-text fallback (no card attachment)"));
    outEl.appendChild(el("div", "fallback", String(reply.text || "(empty reply)")));
  }
}

async function send() {
  const endpoint = $("endpoint").value.trim();
  const message = $("msg").value.trim();
  if (!message) return;
  $("send").disabled = true;
  setStatus("Sending activity and waiting for the card reply…");
  outEl.innerHTML = "";
  try {
    const res = await fetch("send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint, message }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
    setStatus("Reply received.");
    renderReply(data);
  } catch (e) {
    setStatus(String(e.message || e), true);
  } finally {
    $("send").disabled = false;
  }
}

$("send").addEventListener("click", send);
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
for (const c of document.querySelectorAll(".chip")) {
  c.addEventListener("click", () => { $("msg").value = c.dataset.p; send(); });
}
</script>
</body>
</html>`;
}
