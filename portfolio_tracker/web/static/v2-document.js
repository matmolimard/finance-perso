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

async function fetchDocumentDetail() {
  const response = await fetch(`/api/documents/${encodeURIComponent(documentIdFromPath())}`);
  if (!response.ok) {
    throw new Error(`Erreur document (${response.status})`);
  }
  docState.payload = await response.json();
  render();
}

withLoader(fetchDocumentDetail).catch((error) => window.alert(error.message));
