const docState = { payload: null, loading: 0 };

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function tableFromRows(rows, columns) {
  if (!rows.length) {
    return document.querySelector("#v2-empty").innerHTML;
  }
  const head = columns.map(([, label]) => `<th>${esc(label)}</th>`).join("");
  const body = rows.map((row) => `
    <tr>
      ${columns.map(([key, _label, formatter]) => `<td>${formatter ? formatter(row[key], row) : esc(row[key])}</td>`).join("")}
    </tr>
  `).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function loading(active) {
  const overlay = document.querySelector("#v2-loader");
  if (active) {
    docState.loading += 1;
    overlay.classList.remove("is-hidden");
    return;
  }
  docState.loading = Math.max(0, docState.loading - 1);
  if (docState.loading === 0) {
    overlay.classList.add("is-hidden");
  }
}

async function withLoader(task) {
  loading(true);
  try {
    return await task();
  } finally {
    loading(false);
  }
}

function documentIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return decodeURIComponent(parts[parts.length - 1] || "");
}

function render() {
  const documentPayload = docState.payload.document;
  document.querySelector("#document-title").textContent = documentPayload.original_filename;
  document.querySelector("#document-open-raw").href = documentPayload.file_url;
  document.querySelector("#document-open-raw").target = "_blank";
  document.querySelector("#document-open-raw").rel = "noopener noreferrer";
  document.querySelector("#document-frame").src = documentPayload.file_url;

  const rows = [
    { label: "Contrat", value: documentPayload.contract_name || "N/A" },
    { label: "Assureur", value: documentPayload.insurer },
    { label: "Type", value: documentPayload.document_type },
    { label: "Date", value: documentPayload.document_date || "N/A" },
    { label: "Année couverte", value: documentPayload.coverage_year || "N/A" },
    { label: "Statut", value: documentPayload.status },
    { label: "Actif lié", value: documentPayload.asset_id || "N/A" },
    { label: "Notes", value: documentPayload.notes || "N/A" },
  ];
  document.querySelector("#document-meta").innerHTML = tableFromRows(rows, [
    ["label", "Champ"],
    ["value", "Valeur"],
  ]);
}

function formatAmount(value) {
  if (value === null || value === undefined || value === "") return "N/A";
  const amount = Number(value);
  if (Number.isNaN(amount)) return esc(value);
  return `${amount.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} EUR`;
}

function formatUnits(value) {
  if (value === null || value === undefined || value === "") return "N/A";
  const amount = Number(value);
  if (Number.isNaN(amount)) return esc(value);
  return amount.toLocaleString("fr-FR", { minimumFractionDigits: 0, maximumFractionDigits: 6 });
}

function renderArbitrationProposal(proposalPayload) {
  const section = document.querySelector("#arbitration-section");
  if (!section) return;
  section.classList.remove("is-hidden");

  const proposal = proposalPayload.proposal || {};
  const extractionStatus = proposalPayload.extraction_status || "unknown";
  const applicationStatus = proposalPayload.application_status || "pending";
  const candidates = proposal.mapping_candidates || [];
  const options = [
    `<option value="">-- Non mappé --</option>`,
    ...candidates.map((item) => `<option value="${esc(item.position_id)}">${esc(item.label)}</option>`),
  ].join("");
  document.querySelector("#arbitration-summary").innerHTML = `
    <strong>Date</strong>: ${esc(proposal.effective_date || "N/A")} ·
    <strong>Montant</strong>: ${formatAmount(proposal.amount)} ·
    <strong>Extraction</strong>: ${esc(extractionStatus)} ·
    <strong>Application</strong>: ${esc(applicationStatus)}
  `;

  function rowsWithMeta(direction, legs) {
    return (legs || []).map((leg, index) => ({ ...leg, _legIndex: index, _direction: direction }));
  }

  function mappingFormatter(_value, row) {
    const current = String(row.position_id || "");
    const statusClass = row.mapping_status === "matched" ? "ok" : (row.mapping_status === "suggested" ? "warn" : "warn");
    const scoreTag = row.mapping_score ? ` · ${esc(String(row.mapping_score))}` : "";
    return `
      <div style="display:flex; gap:8px; align-items:center;">
        <span class="pill ${statusClass}">${esc(row.mapping_status || "unknown")}${scoreTag}</span>
        <select data-arbitrage-map="1" data-direction="${esc(row._direction)}" data-index="${esc(row._legIndex)}">
          ${options.replace(`value="${esc(current)}"`, `value="${esc(current)}" selected`)}
        </select>
      </div>
    `;
  }

  const legsColumns = [
    ["name", "Support"],
    ["isin", "ISIN"],
    ["units", "Parts", (value) => esc(formatUnits(value))],
    ["nav", "VL", (value) => esc(formatAmount(value))],
    ["amount", "Montant", (value) => esc(formatAmount(value))],
    ["mapping_status", "Mapping", mappingFormatter],
  ];
  const fromRows = rowsWithMeta("from", proposal.from_legs || []);
  const toRows = rowsWithMeta("to", proposal.to_legs || []);
  document.querySelector("#arbitration-from-legs").innerHTML = tableFromRows(fromRows, legsColumns);
  document.querySelector("#arbitration-to-legs").innerHTML = tableFromRows(toRows, legsColumns);

  const hasBlockingUnmatched = [...fromRows, ...toRows].some(
    (leg) => leg.mapping_status !== "matched" && (leg.amount !== null && leg.amount !== undefined || leg.units !== null && leg.units !== undefined),
  );
  const applyButton = document.querySelector("#arbitration-apply-button");
  if (applyButton instanceof HTMLButtonElement) {
    applyButton.disabled = hasBlockingUnmatched;
    if (hasBlockingUnmatched) {
      applyButton.title = "Sauvegarde d'abord le mapping des jambes non mappées";
    } else {
      applyButton.removeAttribute("title");
    }
  }
}

async function fetchArbitrationProposal() {
  const documentPayload = docState.payload.document;
  if (String(documentPayload.document_type) !== "arbitration_letter") return;
  const response = await fetch(`/api/documents/${encodeURIComponent(documentIdFromPath())}/arbitration-proposal`);
  if (!response.ok) {
    throw new Error(`Erreur proposition arbitrage (${response.status})`);
  }
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.error || "Erreur extraction arbitrage");
  }
  renderArbitrationProposal(payload);
}

async function applyArbitration() {
  if (!window.confirm("Appliquer cet arbitrage en base SQLite a partir du PDF ?")) return;
  const button = document.querySelector("#arbitration-apply-button");
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    button.textContent = "Application...";
  }
  try {
    const response = await withLoader(() => fetch(
      `/api/documents/${encodeURIComponent(documentIdFromPath())}/arbitration-apply`,
      { method: "POST" },
    ));
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Erreur application (${response.status})`);
    }
    window.alert(`Arbitrage appliqué: ${payload.created_movements ?? payload.created_lots} mouvement(s) persisté(s), ${payload.skipped_legs} ignoré(s).`);
    await withLoader(fetchArbitrationProposal);
  } catch (error) {
    window.alert(error.message || "Erreur application arbitrage.");
  } finally {
    if (button instanceof HTMLButtonElement) {
      button.disabled = false;
      button.textContent = "Appliquer l'arbitrage";
    }
  }
}

function collectArbitrationMappings() {
  const selects = Array.from(document.querySelectorAll("select[data-arbitrage-map='1']"));
  return selects.map((select) => ({
    direction: String(select.getAttribute("data-direction") || ""),
    index: Number(select.getAttribute("data-index") || "0"),
    position_id: String(select.value || ""),
  }));
}

async function saveArbitrationMappings() {
  const mappings = collectArbitrationMappings();
  const button = document.querySelector("#arbitration-save-mapping-button");
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    button.textContent = "Sauvegarde...";
  }
  try {
    const response = await withLoader(() => fetch(
      `/api/documents/${encodeURIComponent(documentIdFromPath())}/arbitration-map`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mappings }),
      },
    ));
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Erreur mapping (${response.status})`);
    }
    renderArbitrationProposal(payload);
    window.alert("Mappings sauvegardés.");
  } catch (error) {
    window.alert(error.message || "Erreur sauvegarde mapping.");
  } finally {
    if (button instanceof HTMLButtonElement) {
      button.disabled = false;
      button.textContent = "Sauvegarder mappings";
    }
  }
}

async function fetchDocumentDetail() {
  const response = await fetch(`/api/documents/${encodeURIComponent(documentIdFromPath())}`);
  if (!response.ok) {
    throw new Error(`Erreur document (${response.status})`);
  }
  docState.payload = await response.json();
  render();
  await fetchArbitrationProposal();
}

document.querySelector("#arbitration-apply-button")?.addEventListener("click", () => {
  withLoader(applyArbitration).catch((error) => window.alert(error.message));
});
document.querySelector("#arbitration-save-mapping-button")?.addEventListener("click", () => {
  withLoader(saveArbitrationMappings).catch((error) => window.alert(error.message));
});

withLoader(fetchDocumentDetail).catch((error) => window.alert(error.message));
