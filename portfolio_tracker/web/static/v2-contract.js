const state = { payload: null, loading: 0 };

function euro(value) {
  if (typeof value !== "number") return "N/A";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(value);
}

function percent(value) {
  if (typeof value !== "number") return "N/A";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function inputEsc(value) {
  return esc(value).replaceAll("'", "&#39;");
}

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
    state.loading += 1;
    overlay.classList.remove("is-hidden");
    return;
  }
  state.loading = Math.max(0, state.loading - 1);
  if (state.loading === 0) {
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

function contractIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return decodeURIComponent(parts[parts.length - 1] || "");
}

function renderOverview() {
  const { contract, summary } = state.payload;
  document.querySelector("#contract-title").textContent = contract.contract_name;
  document.querySelector("#contract-overview").innerHTML = `
    <article class="metric">
      <div class="metric-label">Valeur actuelle</div>
      <div class="metric-value">${euro(summary.current_value)}</div>
      <div class="metric-sub">${summary.active_positions_count} positions actives</div>
    </article>
    <article class="metric">
      <div class="metric-label">Dernier officiel</div>
      <div class="metric-value">${euro(summary.official_total_value)}</div>
      <div class="metric-sub">${contract.latest_snapshot ? esc(contract.latest_snapshot.reference_date) : "N/A"}</div>
    </article>
    <article class="metric">
      <div class="metric-label">Écart pilotage</div>
      <div class="metric-value ${summary.official_gap >= 0 ? "positive" : "negative"}">${euro(summary.official_gap)}</div>
      <div class="metric-sub">courant vs dernier snapshot</div>
    </article>
    <article class="metric">
      <div class="metric-label">Performance simple</div>
      <div class="metric-value ${contract.performance_simple_amount >= 0 ? "positive" : "negative"}">${euro(contract.performance_simple_amount)}</div>
      <div class="metric-sub">${percent(contract.performance_simple_pct)}</div>
    </article>
  `;
}

function renderSnapshots() {
  document.querySelector("#contract-snapshots").innerHTML = tableFromRows(state.payload.snapshots, [
    ["reference_date", "Réf."],
    ["statement_date", "Relevé"],
    ["official_total_value", "Total", (value) => euro(value)],
    ["official_uc_value", "UC", (value) => euro(value)],
    ["official_fonds_euro_value", "Fonds euro", (value) => euro(value)],
    ["official_euro_interest_net", "Intérêts euro", (value) => euro(value)],
  ]);
}

function renderDocuments() {
  document.querySelector("#contract-documents").innerHTML = tableFromRows(state.payload.documents, [
    ["document_date", "Date"],
    ["document_type", "Type", (value) => `<span class="pill">${esc(value)}</span>`],
    ["original_filename", "Document"],
    ["status", "Statut", (value) => `<span class="pill ok">${esc(value)}</span>`],
    ["document_id", "PDF", (_value, row) => `<a class="ghost-button inline-button" href="/documents/${encodeURIComponent(row.document_id)}">Ouvrir</a>`],
  ]);
}

function renderPilotage() {
  const pilotage = state.payload.fonds_euro_pilotage;
  const summary = state.payload.fonds_euro_pilotage_summary;
  document.querySelector("#pilotage-rate").value = pilotage ? pilotage.annual_rate : "";
  document.querySelector("#pilotage-date").value = pilotage ? pilotage.reference_date : "";
  document.querySelector("#pilotage-notes").value = pilotage?.notes || "";
  if (!summary) {
    document.querySelector("#pilotage-summary").innerHTML = document.querySelector("#v2-empty").innerHTML;
    return;
  }
  const rows = [
    { label: "Valeur de départ officielle", value: euro(summary.start_value) },
    { label: "Flux nets année courante", value: euro(summary.net_flows) },
    { label: "Gain proratisé", value: euro(summary.accrued_gain) },
    { label: "Valeur pilotage", value: euro(summary.pilotage_value) },
    { label: "Nombre de flux", value: summary.flows_count },
  ];
  document.querySelector("#pilotage-summary").innerHTML = tableFromRows(rows, [
    ["label", "Champ"],
    ["value", "Valeur"],
  ]);
}

function documentValidationOptions(current) {
  return ["pending", "confirmed", "rejected"]
    .map((value) => `<option value="${value}" ${current === value ? "selected" : ""}>${value}</option>`)
    .join("");
}

function renderDocumentValidations() {
  const rows = state.payload.documents.map((row) => `
    <tr>
      <td>${esc(row.document_date)}</td>
      <td><span class="pill">${esc(row.document_type)}</span></td>
      <td>${esc(row.original_filename)}</td>
      <td>
        <select class="mini-select doc-validation-status" data-document-id="${esc(row.document_id)}">
          ${documentValidationOptions(row.validation_status || "pending")}
        </select>
      </td>
      <td>
        <input class="mini-input doc-validation-notes" data-document-id="${esc(row.document_id)}" value="${inputEsc(row.validation_notes || "")}">
      </td>
      <td><button class="ghost-button inline-button doc-validation-save" data-document-id="${esc(row.document_id)}" type="button">Sauver</button></td>
    </tr>
  `).join("");
  document.querySelector("#contract-doc-validations").innerHTML = rows
    ? `<table><thead><tr><th>Date</th><th>Type</th><th>Document</th><th>Statut</th><th>Notes</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : document.querySelector("#v2-empty").innerHTML;
}

function renderPositions() {
  const rows = state.payload.positions.map((row) => ({
    ...row,
    label: row.display_name || row.name,
  }));
  document.querySelector("#contract-positions").innerHTML = tableFromRows(rows, [
    ["view_name", "Type", (value) => `<span class="pill">${esc(value)}</span>`],
    ["label", "Support", (_value, row) => `<a class="table-link" href="/supports/${encodeURIComponent(row.position_id)}">${esc(row.label)}</a>`],
    ["position_id", "Position"],
    ["current_value", "Valeur actuelle", (value) => euro(value)],
    ["invested_amount", "Base", (value) => euro(value)],
    ["gain", "Gain", (value) => `<span class="${value >= 0 ? "positive" : "negative"}">${euro(value)}</span>`],
  ]);
}

function render() {
  renderOverview();
  renderPilotage();
  renderDocumentValidations();
  renderSnapshots();
  renderDocuments();
  renderPositions();
}

async function fetchContract() {
  const response = await fetch(`/api/contracts/${encodeURIComponent(contractIdFromPath())}`);
  if (!response.ok) {
    throw new Error(`Erreur contrat (${response.status})`);
  }
  state.payload = await response.json();
  render();
}

async function savePilotage(event) {
  event.preventDefault();
  const payload = {
    annual_rate: document.querySelector("#pilotage-rate").value,
    reference_date: document.querySelector("#pilotage-date").value,
    notes: document.querySelector("#pilotage-notes").value,
  };
  const response = await fetch(`/api/fonds-euro-pilotage/${encodeURIComponent(contractIdFromPath())}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Erreur pilotage (${response.status})`);
  }
  await fetchContract();
}

async function saveDocumentValidation(documentId) {
  const status = document.querySelector(`.doc-validation-status[data-document-id="${CSS.escape(documentId)}"]`).value;
  const notes = document.querySelector(`.doc-validation-notes[data-document-id="${CSS.escape(documentId)}"]`).value;
  const response = await fetch(`/api/documents/${encodeURIComponent(documentId)}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ validation_status: status, notes }),
  });
  if (!response.ok) {
    throw new Error(`Erreur validation document (${response.status})`);
  }
  await fetchContract();
}

document.querySelector("#pilotage-form").addEventListener("submit", (event) => {
  withLoader(() => savePilotage(event)).catch((error) => window.alert(error.message));
});

document.addEventListener("click", (event) => {
  if (!(event.target instanceof HTMLElement) || !event.target.matches(".doc-validation-save")) return;
  const documentId = event.target.dataset.documentId;
  if (!documentId) return;
  withLoader(() => saveDocumentValidation(documentId)).catch((error) => window.alert(error.message));
});

withLoader(fetchContract).catch((error) => window.alert(error.message));
