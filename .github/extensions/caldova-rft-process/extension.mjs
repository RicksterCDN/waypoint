// Extension: caldova-rft-process
// Business-facing canvas explaining how Caldova RFT data and grader were built.

import { createServer } from "node:http";
import { joinSession, createCanvas } from "@github/copilot-sdk/extension";

const servers = new Map();

const processData = {
    title: "From examples to a reliable training set",
    subtitle:
        "A sales-friendly view of how existing example contracts and invoices become RFT training data and a measurable grader.",
    thesis:
        "We started with existing example contracts, invoices, policies, and reviewed outcomes. I generated the repeatable practice set and the business scorecard that checks whether answers stay grounded.",
    talkTrack:
        "We did not make up the business facts. We used existing reviewed examples, turned them into many evidence-review practice questions, and built a grader that checks citations, gaps, and safe advisory behavior.",
    takeaways: [
        {
            label: "Start here",
            value: "Existing examples",
            detail: "Contracts, invoices, policies, outcomes, and citations that were already reviewed.",
        },
        {
            label: "Then generate",
            value: "Practice set",
            detail: "Many review questions with varied wording, grounded in the same trusted examples.",
        },
        {
            label: "Then measure",
            value: "Scorecard",
            detail: "Checks whether answers cite evidence, admit gaps, and avoid fake approvals.",
        },
    ],
    steps: [
        {
            id: "source",
            label: "Source facts",
            short: "Reviewed Caldova evidence is the ground truth.",
            cameFromCaldova:
                "Supplier invoices, contract clauses, policy sections, expected review outcomes, and allowed citations.",
            generated:
                "Nothing synthetic at the fact level. These facts define what the model is allowed to say.",
            howItWorks:
                "Each generated row traces back to reviewed Caldova evidence, so the model learns grounded review behavior instead of generic invoice advice.",
            businessQuestion: "What facts are safe to train on?",
        },
        {
            id: "generator",
            label: "Data-generator logic",
            short: "Reviewed records become repeatable evidence-review tasks.",
            cameFromCaldova:
                "Records that already connect an invoice issue to supporting contract or policy evidence.",
            generated:
                "A repeatable generator that joins invoice context, supplier context, cited evidence, expected support direction, and allowed citations.",
            howItWorks:
                "The generator turns one reviewed case into a structured task: here is the invoice issue, here is the evidence, and here is the evidence JSON the expert should return.",
            businessQuestion: "How does the code create training examples?",
        },
        {
            id: "scenario",
            label: "Scenario rows",
            short: "Same facts, multiple business phrasings.",
            cameFromCaldova:
                "Reviewed invoice scenarios and expected outcomes.",
            generated:
                "Training, validation, and eval rows with varied request wording.",
            howItWorks:
                "Different phrasings ask for the same evidence answer, so the model learns the behavior rather than memorizing a single prompt.",
            businessQuestion: "How do we avoid overfitting to one wording?",
        },
        {
            id: "clauses",
            label: "Clause rows",
            short: "Contract and policy coverage is broadened.",
            cameFromCaldova:
                "Reviewed contract and policy text.",
            generated:
                "Extra clause-grounded review seeds.",
            howItWorks:
                "Clause rows teach the same evidence behavior on more contract and policy situations while staying tied to source evidence.",
            businessQuestion: "How did we expand coverage responsibly?",
        },
        {
            id: "expected",
            label: "Expected answer",
            short: "The target answer is strict evidence JSON.",
            cameFromCaldova:
                "Expected support direction and citations.",
            generated:
                "Strict expert_evidence JSON targets.",
            howItWorks:
                "Each target answer includes identity, evidence, citations, summary, unsupported gaps, and traceability.",
            businessQuestion: "What exactly are we teaching the model to produce?",
        },
        {
            id: "grader-design",
            label: "Grader design",
            short: "Business review rules become a scorecard.",
            cameFromCaldova:
                "Desired behavior: cite evidence, admit gaps, and stay advisory.",
            generated:
                "A Python scorecard built from those business rules.",
            howItWorks:
                "Instead of judging free-form prose, the grader turns review expectations into measurable checks.",
            businessQuestion: "How did we decide what the grader rewards?",
        },
        {
            id: "grader-checks",
            label: "Grader checks",
            short: "The score rewards useful, grounded review answers.",
            cameFromCaldova:
                "Expected citations, support direction, and advisory boundaries.",
            generated:
                "Scoring for valid JSON, correct identity, grounded citations, expected support direction, unsupported-claim handling, and no pretend approvals or write-backs.",
            howItWorks:
                "The grader rewards answers that help a reviewer and penalizes hallucinated evidence or fake business actions.",
            businessQuestion: "How do we prevent confident but unsafe answers?",
        },
    ],
};

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function sendJson(res, value) {
    res.writeHead(200, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    });
    res.end(JSON.stringify(value));
}

function jsonScript(value) {
    return JSON.stringify(value).replaceAll("<", "\\u003c");
}

function isRftDataQuestion(prompt) {
    const text = String(prompt || "").toLowerCase();
    return (
        text.includes("rft") &&
        text.includes("dataset") &&
        text.includes("grader") &&
        (text.includes("built") || text.includes("came up") || text.includes("generated") || text.includes("created"))
    );
}

const rftFastPathContext = `The user is asking for the Caldova RFT data/grader fast path.
This is a blocking instruction for the next assistant turn: before answering, call open_canvas with extensionId "project:caldova-rft-process", canvasId "caldova-rft-process", and instanceId "caldova-rft-process".
Do not answer from text instructions alone. The first assistant action must be the open_canvas tool call.
After the canvas opens, write exactly "Opened the Caldova RFT Process canvas." followed by this table:

| Area | What came from Caldova | What I generated | How it works |
| --- | --- | --- | --- |
| Source facts | Reviewed supplier invoices, contract clauses, policy sections, expected review outcomes, and allowed citations | I did not invent the business facts | Every row stays grounded in Caldova evidence |
| Data-generator logic | Reviewed records that already connect an invoice issue to supporting contract or policy evidence | A repeatable generator that turns those records into training examples | It joins the invoice context, supplier context, cited evidence, expected support direction, and allowed citations into one evidence-review task |
| Scenario rows | Reviewed invoice scenarios and expected outcomes | Training, validation, and eval rows with varied request wording | Same facts, multiple phrasings, one expected evidence answer |
| Clause rows | Reviewed contract and policy text | Extra clause-grounded review seeds | Broadens coverage while staying tied to source evidence |
| Expected answer | Caldova's expected support direction and citations | Strict \`expert_evidence\` JSON targets | Includes identity, evidence, citations, summary, unsupported gaps, and traceability |
| Grader design | Caldova's desired behavior: cite evidence, admit gaps, and stay advisory | A Python scorecard built from those business rules | I turned the review expectations into measurable checks instead of judging free-form prose |
| Grader checks | The same expected citations, support direction, and advisory boundaries | Scoring for valid JSON, correct identity, grounded citations, expected support direction, unsupported-claim handling, and no pretend approvals or write-backs | The grader rewards answers that are useful for review while penalizing hallucinated evidence or fake business actions |

Do not mention internal codenames, commands, model names, job IDs, row counts, file paths, or which docs/files were read. Stay on contract-policy-expert only.`;

function withRftFastPathPrompt(prompt) {
    return `${rftFastPathContext}

Original user question:
${prompt}`;
}

function renderHtml() {
    const pillars = [
        {
            title: "Examples",
            label: "What we started with",
            text: "Existing contracts, invoices, policies, expected outcomes, and citations.",
        },
        {
            title: "Generator",
            label: "What I created",
            text: "A repeatable way to turn those examples into many evidence-review practice questions.",
        },
        {
            title: "Grader",
            label: "How we know it worked",
            text: "A business scorecard for grounded citations, admitted gaps, and safe advisory behavior.",
        },
    ];
    const storyRows = [
        {
            area: "Inputs",
            point: "Start with existing example contracts, invoices, policies, and reviewed outcomes.",
            why: "Sales takeaway: the training set is grounded in real business-style evidence, not invented facts.",
        },
        {
            area: "Generation",
            point: "Generate many practice questions from the same trusted examples.",
            why: "Sales takeaway: the model learns the review behavior, not one memorized prompt.",
        },
        {
            area: "Grader",
            point: "Grade answers against the business rules: cite evidence, admit gaps, stay advisory.",
            why: "Sales takeaway: quality is measured against what a reviewer actually needs.",
        },
    ];
    const graderExamples = [
        {
            label: "Good answer",
            tone: "pass",
            example: "Cites the right clause, states the support direction, and notes any missing evidence.",
            meaning: "Earns credit for being grounded, structured, and review-safe.",
        },
        {
            label: "Partial answer",
            tone: "warn",
            example: "Uses the right JSON shape but gives a vague citation or misses an unsupported gap.",
            meaning: "Gets some credit, but loses points for weak grounding or incomplete uncertainty.",
        },
        {
            label: "Bad answer",
            tone: "fail",
            example: "Claims approval, invents a policy reason, or skips citations.",
            meaning: "Loses heavily because it is unsafe for business review.",
        },
    ];
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${escapeHtml(processData.title)}</title>
  <style>
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--background-color-default, #ffffff);
      color: var(--text-color-default, #1f2328);
      font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
      font-size: var(--text-body-medium, 14px);
      line-height: var(--leading-body-medium, 20px);
    }
    main {
      width: min(1040px, 100%);
      margin: 0 auto;
      padding: 24px;
    }
    .hero {
      display: grid;
      gap: 12px;
      margin-bottom: 18px;
      padding: 22px;
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 24px;
      background:
        radial-gradient(circle at 10% 12%, rgba(84, 174, 255, 0.16), transparent 32%),
        linear-gradient(135deg, rgba(110, 118, 129, 0.08), transparent);
    }
    .eyebrow {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      max-width: 820px;
      font-family: var(--font-sans-display, var(--font-sans, sans-serif));
      font-size: clamp(30px, 5vw, 56px);
      font-weight: var(--font-weight-semibold, 700);
      letter-spacing: -0.055em;
      line-height: 0.98;
    }
    .hero p {
      max-width: 760px;
      margin: 0;
      color: var(--text-color-muted, #57606a);
      font-size: 16px;
      line-height: 23px;
    }
    .talk-track {
      display: grid;
      gap: 6px;
      margin-bottom: 18px;
      padding: 16px 18px;
      border: 1px solid rgba(26, 127, 55, 0.24);
      border-radius: 20px;
      background:
        linear-gradient(90deg, rgba(26, 127, 55, 0.1), rgba(9, 105, 218, 0.08)),
        var(--background-color-default, #ffffff);
    }
    .talk-track span {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .talk-track p {
      margin: 0;
      max-width: 900px;
      font-size: clamp(17px, 2.2vw, 22px);
      font-weight: 650;
      letter-spacing: -0.025em;
      line-height: 1.18;
    }
    .pillars {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }
    .pillar {
      display: grid;
      gap: 10px;
      min-height: 190px;
      padding: 18px;
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 22px;
      background:
        linear-gradient(180deg, rgba(9, 105, 218, 0.1), transparent 44%),
        var(--background-color-default, #ffffff);
      box-shadow: 0 10px 28px rgba(31, 35, 40, 0.07);
    }
    .pillar span {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .pillar strong {
      display: block;
      font-size: clamp(26px, 4vw, 38px);
      line-height: 0.98;
      letter-spacing: -0.04em;
    }
    .pillar p {
      margin: 0;
      color: var(--text-color-muted, #57606a);
      font-size: 14px;
      line-height: 20px;
    }
    .flow {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 18px;
      padding: 12px;
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 22px;
      background: rgba(110, 118, 129, 0.06);
    }
    .flow-node {
      position: relative;
      display: grid;
      gap: 4px;
      min-height: 92px;
      padding: 12px;
      border-radius: 16px;
      background: var(--background-color-default, #ffffff);
      border: 1px solid transparent;
    }
    .flow-node:not(:last-child)::after {
      content: "→";
      position: absolute;
      top: 50%;
      right: -12px;
      transform: translateY(-50%);
      color: var(--text-color-muted, #57606a);
      font-weight: 700;
      z-index: 1;
    }
    .flow-node strong {
      font-size: 14px;
      line-height: 18px;
    }
    .flow-node span {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      line-height: 16px;
    }
    .story {
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 22px;
      background: var(--background-color-default, #ffffff);
      box-shadow: 0 12px 32px rgba(31, 35, 40, 0.08);
      padding: 16px;
      overflow: hidden;
    }
    .examples {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }
    .example {
      display: grid;
      gap: 8px;
      padding: 14px;
      border: 1px solid var(--border-color-default, #d0d7de);
      border-radius: 18px;
      background: var(--background-color-default, #ffffff);
      box-shadow: 0 10px 26px rgba(31, 35, 40, 0.06);
    }
    .example .badge {
      width: fit-content;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .example.pass .badge {
      background: rgba(26, 127, 55, 0.12);
      color: #1a7f37;
    }
    .example.warn .badge {
      background: rgba(154, 103, 0, 0.14);
      color: #9a6700;
    }
    .example.fail .badge {
      background: rgba(207, 34, 46, 0.12);
      color: #cf222e;
    }
    .example strong {
      font-size: 16px;
      line-height: 20px;
    }
    .example p {
      margin: 0;
      color: var(--text-color-muted, #57606a);
      font-size: 13px;
      line-height: 18px;
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      min-width: 680px;
      border-collapse: collapse;
      font-size: 14px;
      line-height: 20px;
    }
    th,
    td {
      padding: 10px;
      border-bottom: 1px solid var(--border-color-default, #d0d7de);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--text-color-muted, #57606a);
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    tr:last-child td {
      border-bottom: 0;
    }
    @media (max-width: 860px) {
      main {
        padding: 12px;
      }
      .pillars,
      .flow,
      .examples {
        grid-template-columns: 1fr;
      }
      .flow-node:not(:last-child)::after {
        content: "↓";
        top: auto;
        right: 14px;
        bottom: -12px;
        transform: none;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero" aria-labelledby="title">
      <div class="eyebrow">RFT process explainer</div>
      <h1 id="title">Reviewed evidence → generated data → measurable grader</h1>
      <p>${escapeHtml(processData.thesis)}</p>
    </section>

    <section class="talk-track" aria-label="Sales talk track">
      <span>30-second explanation</span>
      <p>${escapeHtml(processData.talkTrack)}</p>
    </section>

    <section class="pillars" aria-label="Executive summary">
      ${pillars
          .map(
              (item) => `<article class="pillar">
        <span>${escapeHtml(item.label)}</span>
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.text)}</p>
      </article>`,
          )
          .join("")}
    </section>

    <section class="flow" aria-label="One-screen process">
      <article class="flow-node">
        <strong>Existing examples</strong>
        <span>Contracts, invoices, policies</span>
      </article>
      <article class="flow-node">
        <strong>Generated practice</strong>
        <span>Many grounded review questions</span>
      </article>
      <article class="flow-node">
        <strong>Target answers</strong>
        <span>Cited evidence and gaps</span>
      </article>
      <article class="flow-node">
        <strong>Scorecard</strong>
        <span>Grounded, useful, safe</span>
      </article>
    </section>

    <section class="story" aria-labelledby="table-title">
      <h2 id="table-title">What matters</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Area</th>
              <th>Core point</th>
              <th>Why it matters</th>
            </tr>
          </thead>
          <tbody>
            ${storyRows
                .map(
                    (row) => `<tr>
              <td><strong>${escapeHtml(row.area)}</strong></td>
              <td>${escapeHtml(row.point)}</td>
              <td>${escapeHtml(row.why)}</td>
            </tr>`,
                )
                .join("")}
          </tbody>
        </table>
      </div>
    </section>

    <section class="examples" aria-label="Grader examples">
      ${graderExamples
          .map(
              (item) => `<article class="example ${escapeHtml(item.tone)}">
        <span class="badge">${escapeHtml(item.label)}</span>
        <strong>${escapeHtml(item.example)}</strong>
        <p>${escapeHtml(item.meaning)}</p>
      </article>`,
          )
          .join("")}
    </section>
  </main>
</body>
</html>`;
}

async function startServer(instanceId) {
    const server = createServer((req, res) => {
        const url = new URL(req.url || "/", "http://127.0.0.1");
        if (url.pathname === "/data") {
            sendJson(res, processData);
            return;
        }
        res.writeHead(200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        });
        res.end(renderHtml(instanceId));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    return { server, url: `http://127.0.0.1:${port}/` };
}

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "caldova-rft-process",
            displayName: "Caldova RFT Process",
            description:
                "Interactive business-facing walkthrough of how Caldova RFT data and grader were generated.",
            actions: [
                {
                    name: "get_process",
                    description: "Return the Caldova RFT data and grader process summary.",
                    handler: async () => processData,
                },
                {
                    name: "get_step",
                    description: "Return one Caldova RFT process step by id.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            id: {
                                type: "string",
                                enum: processData.steps.map((step) => step.id),
                            },
                        },
                        required: ["id"],
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const step = processData.steps.find((item) => item.id === ctx.input.id);
                        return step || null;
                    },
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(ctx.instanceId);
                    servers.set(ctx.instanceId, entry);
                }
                return {
                    title: "Caldova RFT Process",
                    status: "Business-facing data and grader walkthrough",
                    url: entry.url,
                };
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
    hooks: {
        onUserPromptSubmitted: async (input) => {
            if (!isRftDataQuestion(input.prompt)) {
                return;
            }
            return {
                modifiedPrompt: withRftFastPathPrompt(input.prompt),
                additionalContext: rftFastPathContext,
            };
        },
        onSessionStart: async (input) => {
            if (!isRftDataQuestion(input.initialPrompt)) {
                return;
            }
            return { additionalContext: rftFastPathContext };
        },
    },
});

void session;
