// ==UserScript==
// @name         M365 Agent Ledger - Add contract-policy-expert
// @namespace    https://github.com/caldova/waypoint
// @version      1.1.3
// @description  Adds or updates contract-policy-expert on the Microsoft 365 admin center Agent Ledger page.
// @match        https://admin.cloud.microsoft/*
// @run-at       document-idle
// @grant        GM_registerMenuCommand
// @grant        GM.registerMenuCommand
// ==/UserScript==

(() => {
  const AGENT = {
    name: 'contract-policy-expert',
    dept: 'Engineering',
    owner: 'J. Deen',
    platform: 'Microsoft Foundry',
    model: 'GPT-5.5',
    spend: 123400,
    projected: 95700,
    billing: 'Mapped',
    status: 'Over budget'
  };

  const ROW_MARKER = 'tm-contract-policy-expert-ledger-row';

  const FOUNDRY_SVG = `
    <svg width="24" height="24" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="16" height="16" rx="3" fill="#F5F4FF"/>
      <path d="M10.62 10.12c.04 0 .08-.1.08-.24V6.04c0-.85.66-1.76 1.58-2.03-.06-.19-.64-2.1-.81-2.63C11.3.82 10.8-.5 10.19-.5c-.05.01-.09.05-.13.13-.32.58-.46 3-.46 4.82v2.25c.27.97.91 3.2.96 3.34 0 0 .03.08.06.08Z" transform="translate(0 1)" fill="#201BA6"/>
      <path d="M14.67 4.9h-1.8c-1.21 0-2.17 1.08-2.17 2.1v3.85c0 .14-.04.24-.08.24s-.09-.1-.09-.1.08.21.18.21h1.97c.62 0 2.47-.61 2.47-2.51V5.45c0-.41-.23-.55-.48-.55Z" fill="#6D71D1"/>
      <path d="M1.5 15.5h5.37c2.34 0 3.19-2.03 3.19-3.79V4.52c0-2.08.18-4.95.6-4.95H7.45c-.77 0-1.43.82-2.18 2.47-.75 1.65-4.35 11.82-4.51 12.3-.26.78-.02 1.16.74 1.16Z" fill="#302EC9"/>
    </svg>
  `;

  const fmtK = (value) =>
    '$' + (value / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 }).replace('.0', '') + 'K';

  const fmtM = (value) =>
    '$' + (value / 1000000).toLocaleString(undefined, { maximumFractionDigits: 1 }).replace('.0', '') + 'M';

  const norm = (text) => (text || '').replace(/\s+/g, ' ').trim().toLowerCase();

  function heading(label) {
    return [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
      .find((el) => norm(el.innerText || el.textContent).includes(label));
  }

  function spans(root) {
    return [...root.querySelectorAll('span')]
      .filter((el) => (el.innerText || el.textContent || '').trim());
  }

  function removeDuplicateIds(root) {
    root.querySelectorAll('[id]').forEach((el) => el.removeAttribute('id'));
  }

  function findLedgerTable() {
    return [...document.querySelectorAll('table')].find((table) => {
      const text = norm(table.innerText || table.textContent);
      return text.includes('dept / owner') && text.includes('projected') && text.includes('billing');
    });
  }

  function configureLedgerRow(row, templateRow, mappedTemplate, overBudgetTemplate) {
    removeDuplicateIds(row);
    row.dataset.tampermonkeyRow = ROW_MARKER;
    row.dataset.selectionIndex = '-1';

    const cells = [...row.children];
    const mappedCells = mappedTemplate ? [...mappedTemplate.children] : [];
    const overBudgetCells = overBudgetTemplate ? [...overBudgetTemplate.children] : [];

    if (cells.length < 9) return false;

    cells[0].innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="width:30px;height:30px;max-width:30px;max-height:30px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:transparent;">
          ${FOUNDRY_SVG}
        </div>
        <div><span style="font-weight:var(--fontWeightSemibold);">${AGENT.name}</span></div>
      </div>
    `;

    cells[1].innerHTML = `
      <div style="font-size: var(--fontSizeBase200); display:flex; align-items:center; gap:4px;">${AGENT.dept}</div>
      <div style="font-size: var(--fontSizeBase100); line-height: var(--lineHeightBase100); color: rgb(97, 97, 97);">${AGENT.owner}</div>
    `;

    cells[2].innerHTML = `<span style="font-size: var(--fontSizeBase200); color: rgb(97, 97, 97);">${AGENT.platform}</span>`;
    cells[3].textContent = AGENT.model;
    cells[4].innerHTML = `12.3B <span style="color: rgb(96, 94, 92);">tokens</span>`;
    cells[5].textContent = fmtK(AGENT.spend);
    cells[6].textContent = fmtK(AGENT.projected);

    if (mappedCells[7]) cells[7].innerHTML = mappedCells[7].innerHTML;
    const billingSpan = [...cells[7].querySelectorAll('span')].at(-1);
    if (billingSpan) billingSpan.textContent = AGENT.billing;
    else cells[7].textContent = AGENT.billing;

    if (overBudgetCells[8]) cells[8].innerHTML = overBudgetCells[8].innerHTML;
    if (!norm(cells[8].innerText || cells[8].textContent).includes('over budget')) {
      cells[8].textContent = AGENT.status;
    }

    return true;
  }

  function updateCustomPlatformLabels(table) {
    [...table.querySelectorAll('tbody tr')].forEach((row) => {
      const platformCell = row.children[2];
      if (!platformCell) return;

      const isCustomA365 = (text) => norm(text).startsWith('custom (a365');
      const replacement =
        isCustomA365(platformCell.textContent) ? 'M365 Copilot' :
        norm(platformCell.textContent) === 'azure ai' ? 'Microsoft Foundry' :
        null;
      if (!replacement) return;

      const textSpan = [...platformCell.querySelectorAll('span')]
        .find((el) => isCustomA365(el.textContent) || norm(el.textContent) === 'azure ai');

      if (textSpan) textSpan.textContent = replacement;
      else platformCell.textContent = replacement;
    });
  }

  function updateLedgerRow() {
    const table = findLedgerTable();
    if (!table) return false;

    updateCustomPlatformLabels(table);

    const rows = [...table.querySelectorAll('tbody tr')];
    const firstDataRow = rows.find((row) => !row.dataset.tampermonkeyRow);
    if (!firstDataRow) return false;

    const mappedTemplate =
      rows.find((row) => !row.dataset.tampermonkeyRow && norm(row.innerText).includes('mapped')) ||
      firstDataRow;

    const overBudgetTemplate =
      rows.find((row) => !row.dataset.tampermonkeyRow && norm(row.innerText).includes('over budget')) ||
      firstDataRow;

    const existing = table.querySelector(`[data-tampermonkey-row="${ROW_MARKER}"]`);
    if (existing) {
      return configureLedgerRow(existing, firstDataRow, mappedTemplate, overBudgetTemplate);
    }

    const row = firstDataRow.cloneNode(true);
    if (!configureLedgerRow(row, firstDataRow, mappedTemplate, overBudgetTemplate)) return false;
    firstDataRow.parentElement.insertBefore(row, firstDataRow);
    return true;
  }

  function findCard(label, marker) {
    const h = heading(label);
    if (!h) return null;

    let card = h.parentElement;
    for (let i = 0; i < 6 && card?.parentElement; i++) {
      if (!marker || (card.innerText || '').includes(marker)) break;
      card = card.parentElement;
    }

    return card;
  }

  function updateTopCostMovers() {
    const card = findCard('top co', 'Explore cost drivers');
    if (!card) return false;

    card.querySelectorAll('svg,img').forEach((el) => el.remove());

    const rowsContainer = [...card.querySelectorAll('div')].find((div) => {
      const rows = [...div.children].filter((child) => child.tagName === 'DIV');
      return rows.length >= 3 &&
        rows.some((row) => norm(row.innerText).includes('agent')) &&
        rows.some((row) => norm(row.innerText).includes('department'));
    });

    if (!rowsContainer) return false;

    const rows = [...rowsContainer.children].filter((child) => child.tagName === 'DIV').slice(0, 3);
    const sampleSpans = [...rows[0].querySelectorAll('span')];

    const labelStrongClass = sampleSpans[0]?.className || 'fui-Body1Strong fui-Text';
    const labelMutedClass = sampleSpans[1]?.className || 'fui-Body1 fui-Text';
    const valueStrongClass = sampleSpans[2]?.className || 'fui-Body1Strong fui-Text';
    const valueMutedClass = sampleSpans[3]?.className || labelMutedClass;

    const data = [
      ['Agent', AGENT.name, '+$123.4K', '(+100%)'],
      ['Department', AGENT.dept, '+$129.5K', '(+612%)'],
      ['Model', AGENT.model, '+$123.4K', '(new)']
    ];

    rows.forEach((row, index) => {
      const [kind, name, amount, pct] = data[index];
      row.innerHTML = '';

      const label = document.createElement('span');
      label.className = labelStrongClass;
      label.append(document.createTextNode(kind + ' '));

      const labelMuted = document.createElement('span');
      labelMuted.className = labelMutedClass;
      labelMuted.style.fontWeight = 'var(--fontWeightRegular)';
      labelMuted.textContent = ' - ' + name;
      label.append(labelMuted);

      const value = document.createElement('span');
      value.className = valueStrongClass;
      value.append(document.createTextNode(amount + ' '));

      const valueMuted = document.createElement('span');
      valueMuted.className = valueMutedClass;
      valueMuted.style.fontWeight = 'var(--fontWeightRegular)';
      valueMuted.textContent = pct;
      value.append(valueMuted);

      row.append(label, value);
    });

    const headline = spans(card).find((el) => /^\+\$/.test((el.textContent || '').trim()));
    if (headline) headline.textContent = '+$123.4K';

    return true;
  }

  function updateEstimatedSpend() {
    const card = findCard('estimated agent', 'View spend outlook') || findCard('e timated agent', 'View spend outlook');
    if (!card) return false;

    const allSpans = spans(card);

    const main = allSpans.find((el) => /^\$\d/.test((el.textContent || '').trim()));
    if (main) main.textContent = '$260.4K';

    const trend = allSpans.find((el) => norm(el.textContent).includes('last month'));
    if (trend) trend.textContent = 'up +124% vs last month';

    const forecast = allSpans.find((el) => norm(el.textContent).includes('on track') || norm(el.textContent).includes('over budget'));
    if (forecast) forecast.textContent = '$372.3K (over budget - 226%)';

    const headroom = allSpans.find((el) => /^\$25K$/i.test((el.textContent || '').trim()) || norm(el.textContent).includes('over'));
    if (headroom) headroom.textContent = '$95.7K over';

    return true;
  }

  function setMetric(card, label, value, subtext) {
    const labelSpan = spans(card).find((el) => norm(el.textContent) === norm(label));
    if (!labelSpan) return false;

    const boxSpans = spans(labelSpan.parentElement);
    if (boxSpans[1]) boxSpans[1].textContent = value;
    if (subtext && boxSpans[2]) boxSpans[2].textContent = subtext;

    return true;
  }

  function updateSpendOutlook() {
    const section = document.querySelector('#forecast-section') || findCard('spend outlook', 'Monthly spend trajectory');
    if (!section) return false;

    setMetric(section, 'Spent YTD', '$953.4K', '40% used');
    setMetric(section, 'Remaining', '$266.6K', 'at current rate');
    setMetric(section, 'Run rate', '$4.5M/yr', '186% of budget');
    setMetric(section, 'Tokens MTD', '12.5B', 'across 6 models');

    const chartValues = [82, 89, 94, 111, 260.4, 372.3];
    const valueLabels = ['$82K', '$89K', '$94K', '$111K', '$260.4K', '$372.3K'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'];
    const scaleMax = 400;
    const bottom = 192;
    const topAtMax = 35.8095238095;
    const y = (value) => bottom - (value / scaleMax) * (bottom - topAtMax);
    const ys = chartValues.map(y);
    const points = chartValues.map((value, index) => `${index * 200},${ys[index]}`);

    const svg = [...section.querySelectorAll('svg')].find((s) => s.getBoundingClientRect().width > 100);
    if (svg) {
      svg.querySelectorAll('[data-tm-chart-polish="true"]').forEach((el) => el.remove());

      const area = svg.querySelector('path[fill^="url"]');
      const lines = [...svg.querySelectorAll('polyline')];

      if (area) area.setAttribute('d', `M0,${bottom} L${points.join(' L')} L1000,${bottom} Z`);

      if (lines[0]) {
        lines[0].setAttribute('points', points.slice(0, 4).join(' '));
        lines[0].setAttribute('stroke', '#2B6CB0');
        lines[0].setAttribute('stroke-width', '3');
        lines[0].removeAttribute('stroke-dasharray');
      }

      if (lines[1]) {
        lines[1].setAttribute('points', points.slice(3).join(' '));
        lines[1].setAttribute('stroke', '#63B3ED');
        lines[1].setAttribute('stroke-width', '3');
        lines[1].setAttribute('stroke-dasharray', '6 5');
      }
    }

    const allSpans = spans(section);

    const replacements = new Map([
      ['$200K', '$400K'],
      ['$160K', '$320K'],
      ['$120K', '$240K'],
      ['$80K', '$160K'],
      ['$175K', '$260.4K'],
      ['$180K', '$372.3K'],
      ['$260K', '$260.4K'],
      ['$372K', '$372.3K']
    ]);

    allSpans.forEach((el) => {
      const text = (el.textContent || '').trim();
      if (replacements.has(text)) el.textContent = replacements.get(text);
    });

    valueLabels.forEach((label, index) => {
      const labelEl = allSpans.find((el) => (el.textContent || '').trim() === label);
      if (labelEl?.parentElement?.style) {
        labelEl.parentElement.style.top = Math.max(24, ys[index] - 19) + 'px';
      }
    });

    const monthSpans = months.map((month) => allSpans.find((el) => (el.textContent || '').trim() === month)).filter(Boolean);
    const chartBox = monthSpans[0]?.parentElement;
    const markerParents = chartBox
      ? [...chartBox.children].filter((child) => child.querySelector?.('div[style*="border-radius: 50%"]') && child.style.position === 'absolute')
      : [];

    markerParents.slice(0, 6).forEach((parent, index) => {
      const size = index === 3 ? 10 : 6;
      parent.style.top = Math.max(24, ys[index]) + 'px';

      const dot = parent.querySelector('div[style*="border-radius: 50%"]');
      if (dot) {
        dot.style.width = size + 'px';
        dot.style.height = size + 'px';
        dot.style.borderRadius = '50%';
        dot.style.backgroundColor = index <= 3 ? 'rgb(43, 108, 176)' : 'rgb(99, 179, 237)';
        if (index === 3) {
          dot.style.border = '2px solid rgb(255, 255, 255)';
          dot.style.boxShadow = 'rgb(43, 108, 176) 0px 0px 0px 2px';
        } else {
          dot.style.border = 'none';
          dot.style.boxShadow = 'none';
        }
      }
    });

    return true;
  }

  function updateModelUsage() {
    const section = findCard('model u', 'total this month') || findCard('model usage', 'total this month');
    if (!section) return false;

    const caption = spans(section).find((el) => norm(el.textContent).includes('total this month'));
    if (caption) caption.textContent = '12.5B total this month - 6 models active';

    const rowsContainer = [...section.querySelectorAll('div')].find((div) => {
      const children = [...div.children].filter((child) => child.tagName === 'DIV');
      return children.length === 5 && children.every((child) => child.querySelector('div[style*="height: 10px"]'));
    });

    if (!rowsContainer) return false;

    rowsContainer.style.display = 'flex';
    rowsContainer.style.flexDirection = 'column';
    rowsContainer.style.justifyContent = 'space-between';
    rowsContainer.style.flex = '1 1 0%';
    rowsContainer.style.gap = 'var(--spacingVerticalL)';
    rowsContainer.style.removeProperty('width');
    rowsContainer.style.removeProperty('background-color');

    const data = [
      ['GPT-5.5', '12.3B (98.9%)', '98.9%', 'rgb(48, 46, 201)'],
      ['GPT-5.4', '75.3M (0.6%)', '0.6%', 'rgb(43, 108, 176)'],
      ['Claude 4.6 Sonnet', '47.4M (0.4%)', '0.4%', 'rgb(99, 179, 237)'],
      ['Gemini 3.1 Pro', '4.8M (<0.1%)', '1%', 'rgb(128, 90, 213)'],
      ['Llama 4 Maverick', '2.9M (<0.1%)', '1%', 'rgb(221, 107, 32)']
    ];

    [...rowsContainer.children].filter((child) => child.tagName === 'DIV').slice(0, 5).forEach((row, index) => {
      const [name, value, width, color] = data[index];

      row.style.removeProperty('width');
      row.style.removeProperty('background-color');

      const header = [...row.children].find((child) => (child.style?.justifyContent || '').includes('space-between')) || row.firstElementChild;
      if (header) {
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        header.style.marginBottom = 'var(--spacingVerticalXS)';

        const labels = [...header.querySelectorAll('span')];
        if (labels[0]) labels[0].textContent = name;
        if (labels[1]) labels[1].textContent = value;
      }

      const track = [...row.children].find((child) => (child.getAttribute('style') || '').includes('height: 10px'));
      if (track) {
        track.style.height = '10px';
        track.style.backgroundColor = 'rgb(240, 244, 248)';
        track.style.borderRadius = '5px';
        track.style.overflow = 'hidden';

        const fill = track.firstElementChild;
        if (fill) {
          fill.style.height = '100%';
          fill.style.width = width;
          fill.style.backgroundColor = color;
          fill.style.borderRadius = '5px';
          fill.style.transition = 'width 0.6s';
        }
      }
    });

    return true;
  }

  function updateBudgetGovernance() {
    const card = findCard('budget governance', 'Resolve escalations');
    if (!card) return false;

    const allSpans = spans(card);
    const count = allSpans.find((el) => (el.textContent || '').trim() === '2/6' || (el.textContent || '').trim() === '4/6');
    if (count) count.textContent = '4/6';

    const engineering = allSpans.find((el) => norm(el.textContent).includes('engineering dept'));
    if (engineering) allSpans[allSpans.indexOf(engineering) + 1].textContent = '$150.4K / $20K';

    const marketing = allSpans.find((el) => norm(el.textContent).includes('marketing dept'));
    if (marketing) allSpans[allSpans.indexOf(marketing) + 1].textContent = '$11K / $10K';

    const third = allSpans.find((el) => norm(el.textContent).includes('it op') || norm(el.textContent).includes('gpt-5.5'));
    if (third) {
      third.textContent = 'GPT-5.5 model';
      allSpans[allSpans.indexOf(third) + 1].textContent = '12.3B / 10B tokens';
    }

    return true;
  }

  function setEscalationCell(cell, text, critical) {
    const span = cell.querySelector('span') || cell;
    span.textContent = text;
    span.style.color = critical ? 'rgb(196, 49, 75)' : 'rgb(97, 97, 97)';
    span.style.fontWeight = critical ? '600' : '400';
  }

  function setBudgetBar(cell, percent, exceeded) {
    const wrapper = cell.querySelector(':scope > div');
    const track = wrapper?.querySelector(':scope > div');
    const fill = track?.querySelector(':scope > div');
    const label = wrapper?.querySelector(':scope > span');

    if (wrapper) wrapper.removeAttribute('style');
    if (track) track.setAttribute('style', 'width: 60px;');
    if (fill) {
      fill.setAttribute(
        'style',
        `width: ${Math.min(percent, 100)}%; background-color: ${exceeded ? 'rgb(196, 49, 75)' : 'rgb(14, 112, 14)'};`
      );
    }
    if (label) {
      label.removeAttribute('style');
      label.textContent = percent + '%';
    }
  }

  function updateBudgetPolicies() {
    const section = document.querySelector('#budgets-section') || findCard('budget polic', 'Policy');
    const table = section?.querySelector('table');
    if (!section || !table) return false;

    const badge = section.querySelector('h2 span');
    if (badge) badge.textContent = '4 pending escalations';

    const rows = [...table.querySelectorAll('tbody tr')];
    const byName = (name) => rows.find((row) => norm(row.children[0]?.innerText || row.children[0]?.textContent).includes(norm(name)));

    const engineering = byName('Engineering dept');
    const marketing = byName('Marketing dept');
    const enterprise = byName('Enterprise default');
    const gpt = byName('GPT-4o usage cap') || byName('GPT-5.5 usage cap');
    const it = byName('IT Ops');
    const finance = byName('Finance dept');

    const setStatus = (row, status) => {
      const cell = row.children[7];
      const dot = cell.querySelector('span span');
      const textNode = [...cell.querySelectorAll('span')].at(-1);
      if (textNode) textNode.childNodes.forEach((node) => {
        if (node.nodeType === Node.TEXT_NODE) node.textContent = status;
      });
      if (!norm(cell.innerText || cell.textContent).includes(norm(status))) cell.textContent = status;
      if (dot) dot.style.backgroundColor = status === 'Active' ? 'rgb(16, 124, 16)' : 'rgb(196, 49, 75)';
    };

    if (enterprise) {
      const c = enterprise.children;
      enterprise.style.backgroundColor = 'rgb(253, 246, 236)';
      setEscalationCell(c[1], '1 pending request', true);
      c[3].textContent = '$200K / org / mo';
      c[4].textContent = '$249.4K';
      setBudgetBar(c[5], 125, true);
      c[6].textContent = 'Throttle + alert';
      setStatus(enterprise, 'Exceeded');
    }

    if (engineering) {
      const c = engineering.children;
      engineering.style.backgroundColor = 'rgb(253, 246, 236)';
      setEscalationCell(c[1], '1 pending request', true);
      c[3].textContent = '$20K / dept / mo';
      c[4].textContent = '$150.4K';
      setBudgetBar(c[5], 752, true);
      c[6].textContent = 'Throttle + alert';
      setStatus(engineering, 'Exceeded');
    }

    if (marketing) {
      const c = marketing.children;
      marketing.style.backgroundColor = 'rgb(253, 246, 236)';
      setEscalationCell(c[1], '1 pending request', true);
      c[4].textContent = '$11K';
      setBudgetBar(c[5], 110, true);
      c[6].textContent = 'Throttle + alert';
      setStatus(marketing, 'Exceeded');
    }

    if (gpt) {
      const c = gpt.children;
      gpt.style.backgroundColor = 'rgb(253, 246, 236)';
      c[0].textContent = 'GPT-5.5 usage cap';
      setEscalationCell(c[1], '1 pending request', true);
      (c[2].querySelector('span') || c[2]).textContent = 'Per model';
      c[3].textContent = '10B tokens / org / mo';
      c[4].textContent = '12.3B used';
      if (engineering?.children[5]) c[5].innerHTML = engineering.children[5].innerHTML;
      setBudgetBar(c[5], 123, true);
      c[6].textContent = 'Route to lower-cost model';
      setStatus(gpt, 'Exceeded');
    }

    if (it) {
      it.style.backgroundColor = '';
      setEscalationCell(it.children[1], 'Auto-approve', false);
      setBudgetBar(it.children[5], 83, false);
      setStatus(it, 'Active');
    }

    if (finance) {
      finance.style.backgroundColor = '';
      setEscalationCell(finance.children[1], 'Auto-approve', false);
      setBudgetBar(finance.children[5], 73, false);
      setStatus(finance, 'Active');
    }

    return true;
  }

  function run() {
    if (!location.hash.toLowerCase().includes('/agents/ledger')) {
      alert('Go to Agent Ledger first, then run this command.');
      return;
    }

    const results = [
      updateLedgerRow(),
      updateTopCostMovers(),
      updateEstimatedSpend(),
      updateSpendOutlook(),
      updateModelUsage(),
      updateBudgetGovernance(),
      updateBudgetPolicies()
    ];

    alert(results.every(Boolean)
      ? 'Updated contract-policy-expert ledger prototype.'
      : 'Updated what I could find. If something is missing, expand all Agent Ledger sections and run again.');
  }

  function registerMenuCommand(name, callback) {
    if (typeof GM_registerMenuCommand === 'function') {
      GM_registerMenuCommand(name, callback);
      return;
    }
    if (typeof GM !== 'undefined' && typeof GM.registerMenuCommand === 'function') {
      GM.registerMenuCommand(name, callback);
    }
  }

  registerMenuCommand('Add / update contract-policy-expert ledger', run);
})();