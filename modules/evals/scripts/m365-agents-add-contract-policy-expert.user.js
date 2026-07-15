// ==UserScript==
// @name         M365 Agents - Add contract-policy-expert
// @namespace    https://github.com/caldova/waypoint
// @version      1.0.9
// @description  Registers a manual command to update the Microsoft 365 admin center All agents table demo rows.
// @match        https://admin.cloud.microsoft/*
// @run-at       document-idle
// @noframes
// @grant        GM_registerMenuCommand
// @grant        GM.registerMenuCommand
// ==/UserScript==

(function () {
  "use strict";

  const AGENT = {
    name: "contract-policy-expert",
    status: "Available",
    platform: "Microsoft Foundry",
    risks: "0",
    activeUsers: "5",
    totalSessions: "1256",
    dateAdded: "July 7, 2026",
    lastActive: "Never",
  };
  const AGENT_RENAMES = [
    {
      from: "Contract Intelligence",
      to: "Finance Intelligence",
    },
  ];

  const MARKER = "contract-policy-expert";
  const RENDER_SIGNATURE = [
    AGENT.name,
    AGENT.status,
    AGENT.platform,
    AGENT.risks,
    AGENT.activeUsers,
    AGENT.totalSessions,
    AGENT.dateAdded,
    AGENT.lastActive,
  ].join("|");
  const PRODUCT_BACKLOG_ICON_SRC = "data:image/svg+xml,%3Csvg%20width%3D%2232%22%20height%3D%2232%22%20viewBox%3D%220%200%2016%2016%22%20fill%3D%22none%22%20xmlns%3D%22http%3A//www.w3.org/2000/svg%22%3E%3Crect%20width%3D%2216%22%20height%3D%2216%22%20rx%3D%223%22%20fill%3D%22%23F5F4FF%22/%3E%3Cpath%20d%3D%22M10.62%2010.12c.04%200%20.08-.1.08-.24V6.04c0-.85.66-1.76%201.58-2.03-.06-.19-.64-2.1-.81-2.63C11.3.82%2010.8-.5%2010.19-.5c-.05.01-.09.05-.13.13-.32.58-.46%203-.46%204.82v2.25c.27.97.91%203.2.96%203.34%200%200%20.03.08.06.08Z%22%20transform%3D%22translate(0%201)%22%20fill%3D%22%23201BA6%22/%3E%3Cpath%20d%3D%22M14.67%204.9h-1.8c-1.21%200-2.17%201.08-2.17%202.1v3.85c0%20.14-.04.24-.08.24s-.09-.1-.09-.1.08.21.18.21h1.97c.62%200%202.47-.61%202.47-2.51V5.45c0-.41-.23-.55-.48-.55Z%22%20fill%3D%22%236D71D1%22/%3E%3Cpath%20d%3D%22M1.5%2015.5h5.37c2.34%200%203.19-2.03%203.19-3.79V4.52c0-2.08.18-4.95.6-4.95H7.45c-.77%200-1.43.82-2.18%202.47-.75%201.65-4.35%2011.82-4.51%2012.3-.26.78-.02%201.16.74%201.16Z%22%20fill%3D%22%23302EC9%22/%3E%3C/svg%3E";

  function isAllAgentsPage() {
    return location.hash.toLowerCase().startsWith("#/agents/all");
  }

  function isVisible(element) {
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function getDataRows() {
    return Array.from(document.querySelectorAll('[role="row"], .ms-DetailsRow'))
      .filter((row) => isVisible(row))
      .filter((row) => !row.querySelector('[role="columnheader"]'))
      .filter((row) => row.querySelector('[role="gridcell"], [role="cell"], .ms-DetailsRow-cell'));
  }

  function getCells(row) {
    return Array.from(row.querySelectorAll('[role="gridcell"], [role="cell"], .ms-DetailsRow-cell'));
  }

  function setCellText(cell, value) {
    if (!cell) {
      return;
    }
    if (visibleText(cell) !== value) {
      cell.textContent = value;
    }
    cell.setAttribute("title", value);
  }

  function visibleText(element) {
    return (element.textContent || "").replace(/\s+/g, " ").trim();
  }

  function createProductBacklogAvatar() {
    const wrapper = document.createElement("div");
    wrapper.className = "ms-Image";
    wrapper.setAttribute("aria-hidden", "true");
    wrapper.style.height = "30px";
    wrapper.style.marginRight = "8px";
    wrapper.style.width = "30px";

    const image = document.createElement("img");
    image.src = PRODUCT_BACKLOG_ICON_SRC;
    image.role = "presentation";
    image.alt = "";
    image.className = "ms-Image-image is-loaded ms-Image-image--contain ms-Image-image--portrait is-fadeIn";
    wrapper.append(image);
    return wrapper;
  }

  function setPlatformCell(cell) {
    if (!cell) {
      return;
    }
    cell.querySelectorAll(".ms-Image, img").forEach((node) => node.remove());
    setCellText(cell, AGENT.platform);
    cell.setAttribute("title", AGENT.platform);
  }

  function removeSourceInstanceBadges(cell) {
    Array.from(cell.querySelectorAll("span, div"))
      .filter((element) => {
        const text = visibleText(element);
        return (
          element.closest("button") === null &&
          !element.querySelector("button") &&
          ((text.includes("Your org") && text.includes("Microsoft Foundry")) ||
          text === "0" ||
          text.includes("This agent template has")
          )
        );
      })
      .forEach((element) => element.remove());
  }

  function updateNameCellButton(cell) {
    const button =
      cell.querySelector('button[aria-label]:not([aria-haspopup="true"])') ||
      cell.querySelector("button[aria-label]");
    if (button) {
      button.setAttribute("aria-label", AGENT.name);
      button.setAttribute("title", AGENT.name);
    }
  }

  function updateNameCellIcon(cell) {
    let image = cell.querySelector(".ms-Image > img") || cell.querySelector("img");
    if (!image) {
      const avatar = createProductBacklogAvatar();
      const insertBefore = cell.querySelector("button, span, div");
      if (insertBefore) {
        insertBefore.parentElement.insertBefore(avatar, insertBefore);
      } else {
        cell.prepend(avatar);
      }
      image = avatar.querySelector("img");
    }

    const wrapper = image.closest(".ms-Image");
    if (wrapper) {
      wrapper.style.display = "inline-flex";
      wrapper.style.height = "30px";
      wrapper.style.minWidth = "30px";
      wrapper.style.width = "30px";
      wrapper.setAttribute("aria-hidden", "true");
    }
    image.src = PRODUCT_BACKLOG_ICON_SRC;
    image.role = "presentation";
    image.alt = "";
    image.className = "ms-Image-image is-loaded ms-Image-image--contain ms-Image-image--portrait is-fadeIn";
    image.style.display = "block";
    image.style.height = "30px";
    image.style.width = "30px";
  }

  function updateNameText(cell) {
    const button = cell.querySelector("button");
    const textScope = button || cell;
    const originalName = button?.getAttribute("aria-label");
    const labels = Array.from(textScope.querySelectorAll("span"));
    const existingLabel =
      labels.find((element) => originalName && visibleText(element) === originalName) ||
      labels.find((element) => {
        const text = visibleText(element);
        return /[A-Za-z]/.test(text) && text !== "Available" && !text.includes("Your org") && !text.includes("Microsoft Foundry");
      });

    if (existingLabel) {
      existingLabel.textContent = AGENT.name;
      return;
    }

    const label = document.createElement("span");
    label.textContent = AGENT.name;
    cell.append(label);
  }

  function setNameCell(cell) {
    if (!cell) {
      return;
    }
    removeSourceInstanceBadges(cell);
    updateNameText(cell);
    updateNameCellButton(cell);
    updateNameCellIcon(cell);
    cell.setAttribute("title", AGENT.name);
  }

  function renameAgentNameCell(cell, fromName, toName) {
    if (!cell) {
      return false;
    }

    let renamed = false;
    const isSourceName = (value) => value.replace(/\s+/g, " ").trim().toLowerCase() === fromName.toLowerCase();
    cell.querySelectorAll("button[aria-label], [title]").forEach((element) => {
      if (isSourceName(element.getAttribute("aria-label") || "")) {
        element.setAttribute("aria-label", toName);
        renamed = true;
      }
      if (isSourceName(element.getAttribute("title") || "")) {
        element.setAttribute("title", toName);
        renamed = true;
      }
    });

    const textNodes = Array.from(cell.querySelectorAll("span, div, button"))
      .filter((element) => Array.from(element.children).every((child) => !isSourceName(visibleText(child))))
      .filter((element) => isSourceName(visibleText(element)));

    textNodes.forEach((element) => {
      element.textContent = toName;
      renamed = true;
    });

    if (cell.getAttribute("title") === fromName || visibleText(cell).includes(toName)) {
      cell.setAttribute("title", toName);
    }
    return renamed;
  }

  function renameExistingAgents() {
    let renamed = false;
    for (const row of getDataRows()) {
      if (row.dataset.tampermonkeyRow === MARKER) {
        continue;
      }

      const nameCell = getCells(row)[0];
      const nameText = visibleText(nameCell);
      const rename = AGENT_RENAMES.find(({ from }) =>
        nameText.toLowerCase().includes(from.toLowerCase())
      );
      if (rename) {
        renamed = renameAgentNameCell(nameCell, rename.from, rename.to) || renamed;
      }
    }
    return renamed;
  }

  function createAvailableStatusContent() {
    const stack = document.createElement("div");
    stack.className = "ms-Stack css-710";

    const icon = document.createElement("i");
    icon.setAttribute("data-icon-name", "SkypeCircleCheck");
    icon.setAttribute("aria-hidden", "true");
    icon.className = "root-717";
    icon.textContent = "";

    stack.append(icon, document.createTextNode(AGENT.status));
    return stack;
  }

  function setStatusCell(cell, templateCell) {
    if (!cell) {
      return;
    }

    if (templateCell) {
      cell.innerHTML = templateCell.innerHTML;
    } else {
      cell.replaceChildren(createAvailableStatusContent());
    }
    cell.setAttribute("title", AGENT.status);
  }

  function writeAgentRow(row, availableStatusCell) {
    if (isRendered(row)) {
      return false;
    }

    row.dataset.tampermonkeyRow = MARKER;
    row.setAttribute("data-tampermonkey-row", MARKER);
    row.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));

    const cells = getCells(row);
    setNameCell(cells[0]);
    setStatusCell(cells[1], availableStatusCell);
    setPlatformCell(cells[2]);
    setCellText(cells[3], AGENT.risks);
    setCellText(cells[4], AGENT.activeUsers);
    setCellText(cells[5], AGENT.totalSessions);
    setCellText(cells[6], AGENT.dateAdded);
    setCellText(cells[7], AGENT.lastActive);
    row.dataset.tampermonkeySignature = RENDER_SIGNATURE;
    return true;
  }

  function ensureCellCount(row, sourceRow) {
    const fields = row.querySelector(".ms-DetailsRow-fields") || getCells(row)[0]?.parentElement;
    if (!fields) {
      return;
    }

    let cells = getCells(row);
    const sourceCells = getCells(sourceRow);
    while (cells.length < sourceCells.length) {
      fields.append(sourceCells[cells.length].cloneNode(true));
      cells = getCells(row);
    }
  }

  function makeFirstCellResponsive(cells, sourceCells) {
    if (cells.length < 2) {
      return;
    }

    const fixedWidth = (sourceCells || cells)
      .slice(1)
      .reduce((total, cell) => total + Math.round(cell.getBoundingClientRect().width || parseFloat(cell.style.width) || 0), 0);
    if (fixedWidth > 0) {
      const fields = cells[0].parentElement;
      if (fields) {
        fields.style.width = "100%";
      }
      cells[0].style.width = `calc(100% - ${fixedWidth}px)`;
    }
  }

  function isRendered(row) {
    if (row.dataset.tampermonkeySignature !== RENDER_SIGNATURE) {
      return false;
    }
    const cells = getCells(row);
    return (
      cells[0]?.querySelector(".ms-Image > img")?.getAttribute("src") === PRODUCT_BACKLOG_ICON_SRC &&
      Boolean(cells[1]?.querySelector('[data-icon-name="SkypeCircleCheck"]')) &&
      !cells[2]?.querySelector(".ms-Image, img") &&
      visibleText(cells[0]).includes(AGENT.name) &&
      visibleText(cells[1]) === AGENT.status &&
      visibleText(cells[2]) === AGENT.platform &&
      visibleText(cells[3]) === AGENT.risks &&
      visibleText(cells[4]) === AGENT.activeUsers &&
      visibleText(cells[5]) === AGENT.totalSessions &&
      visibleText(cells[6]) === AGENT.dateAdded &&
      visibleText(cells[7]) === AGENT.lastActive
    );
  }

  function addOrUpdateContractPolicyExpertRow() {
    if (!isAllAgentsPage()) {
      return false;
    }

    const renamed = renameExistingAgents();
    document.querySelectorAll(`[data-tampermonkey-row="${MARKER}"]`).forEach((row) => row.remove());

    const firstDataRow = getDataRows().find((row) => row.dataset.tampermonkeyRow !== MARKER);
    if (!firstDataRow) {
      return renamed;
    }

    const availableStatusCell = getDataRows()
      .map((row) => getCells(row)[1])
      .find((cell) => visibleText(cell).includes(AGENT.status));

    const row = firstDataRow.cloneNode(true);
    ensureCellCount(row, firstDataRow);
    writeAgentRow(row, availableStatusCell);
    firstDataRow.parentElement.insertBefore(row, firstDataRow);
    makeFirstCellResponsive(getCells(row), getCells(firstDataRow));
    return true;
  }

  function registerMenuCommand(name, callback) {
    if (typeof GM_registerMenuCommand === "function") {
      GM_registerMenuCommand(name, callback);
      return;
    }
    if (typeof GM !== "undefined" && typeof GM.registerMenuCommand === "function") {
      GM.registerMenuCommand(name, callback);
    }
  }

  registerMenuCommand("Add contract-policy-expert row", addOrUpdateContractPolicyExpertRow);
})();
