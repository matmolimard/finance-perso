const gedState = { payload: null, loading: 0 };

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
    gedState.loading += 1;
    overlay.classList.remove("is-hidden");
    return;
  }
  gedState.loading = Math.max(0, gedState.loading - 1);
  if (gedState.loading === 0) {
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

function populateSelect(node, values, current) {
  const options = ['<option value="">Tous</option>']
    .concat(values.map((value) => `<option value="${esc(value)}" ${String(current ?? "") === String(value) ? "selected" : ""}>${esc(value)}</option>`));
  node.innerHTML = options.join("");
}

function populateUploadContractSelect(values) {
  const node = document.querySelector("#ged-upload-contract");
  if (!node) return;
  const current = node.value;
  const options = ['<option value="">Auto</option>']
    .concat(values.map((value) => `<option value="${esc(value)}" ${String(current ?? "") === String(value) ? "selected" : ""}>${esc(value)}</option>`));
  node.innerHTML = options.join("");
}

function renderFilters() {
  const { options, filters } = gedState.payload;
  populateSelect(document.querySelector("#ged-contract"), options.contract_names, filters.contract_name);
  populateSelect(document.querySelector("#ged-type"), options.document_types, filters.document_type);
  populateSelect(document.querySelector("#ged-year"), options.years, filters.year);
  populateSelect(document.querySelector("#ged-status"), options.statuses, filters.status);
  populateUploadContractSelect(options.contract_names);
}

function renderDocuments() {
  document.querySelector("#ged-documents").innerHTML = tableFromRows(gedState.payload.documents, [
    ["contract_name", "Contrat"],
    ["document_date", "Date"],
    ["document_type", "Type", (value) => `<span class="pill">${esc(value)}</span>`],
    ["coverage_year", "Année"],
    ["original_filename", "Document"],
    ["status", "Statut", (value) => `<span class="pill ok">${esc(value)}</span>`],
    ["document_id", "PDF", (_value, row) => `<a class="ghost-button inline-button" href="/documents/${encodeURIComponent(row.document_id)}">Ouvrir</a>`],
  ]);
}

function render() {
  renderFilters();
  renderDocuments();
}

function currentQuery() {
  const params = new URLSearchParams();
  const contract = document.querySelector("#ged-contract").value;
  const type = document.querySelector("#ged-type").value;
  const year = document.querySelector("#ged-year").value;
  const status = document.querySelector("#ged-status").value;
  if (contract) params.set("contract_name", contract);
  if (type) params.set("document_type", type);
  if (year) params.set("year", year);
  if (status) params.set("status", status);
  return params.toString();
}

async function fetchGed() {
  const query = currentQuery();
  const response = await fetch(`/api/ged${query ? `?${query}` : ""}`);
  if (!response.ok) {
    throw new Error(`Erreur GED (${response.status})`);
  }
  gedState.payload = await response.json();
  render();
}

function showUploadResult(message, kind) {
  const box = document.querySelector("#ged-upload-result");
  if (!box) return;
  box.classList.remove("is-hidden", "success", "error");
  box.classList.add(kind === "error" ? "error" : "success");
  box.innerHTML = esc(message);
}

function clearUploadResult() {
  const box = document.querySelector("#ged-upload-result");
  if (!box) return;
  box.classList.add("is-hidden");
  box.classList.remove("success", "error");
  box.innerHTML = "";
}

async function submitUploadForm(event) {
  event.preventDefault();
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  clearUploadResult();

  const fileInput = document.querySelector("#ged-upload-file");
  if (!(fileInput instanceof HTMLInputElement) || !fileInput.files || !fileInput.files.length) {
    showUploadResult("Veuillez sélectionner un fichier PDF.", "error");
    return;
  }
  const file = fileInput.files[0];
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showUploadResult("Seuls les fichiers PDF sont supportés.", "error");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  const optionalFields = ["contract_name", "insurer", "document_date", "status", "notes"];
  for (const name of optionalFields) {
    const value = String((new FormData(form).get(name) ?? "")).trim();
    if (value) formData.append(name, value);
  }

  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = true;
    submitButton.textContent = "Import en cours...";
  }

  try {
    const response = await withLoader(() => fetch("/api/documents/upload", {
      method: "POST",
      body: formData,
    }));
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Erreur upload (${response.status})`);
    }

    const doc = payload.document || {};
    const duplicate = payload.duplicate === true;
    const suffix = duplicate ? " (doublon détecté)" : "";
    showUploadResult(`Document importé: ${doc.document_id || "ok"}${suffix}`, "success");
    if (!duplicate) {
      form.reset();
    }
    await withLoader(fetchGed);
  } catch (error) {
    showUploadResult(error.message || "Erreur lors de l'import.", "error");
  } finally {
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = false;
      submitButton.textContent = "Importer";
    }
  }
}

for (const selector of ["#ged-contract", "#ged-type", "#ged-year", "#ged-status"]) {
  document.addEventListener("change", (event) => {
    if (!(event.target instanceof HTMLElement) || !event.target.matches(selector)) return;
    withLoader(fetchGed).catch((error) => window.alert(error.message));
  });
}

document.querySelector("#ged-upload-form")?.addEventListener("submit", submitUploadForm);

withLoader(fetchGed).catch((error) => window.alert(error.message));
