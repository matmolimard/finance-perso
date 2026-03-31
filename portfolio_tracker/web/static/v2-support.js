const supportState = { payload: null, loading: 0 };

function euro(value) {
  if (typeof value !== "number") return "N/A";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(value);
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

function inputEsc(value) {
  return esc(value).replaceAll("'", "&#39;");
}

function loading(active) {
  const overlay = document.querySelector("#v2-loader");
  if (active) {
    supportState.loading += 1;
    overlay.classList.remove("is-hidden");
    return;
  }
  supportState.loading = Math.max(0, supportState.loading - 1);
  if (supportState.loading === 0) {
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

function positionIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return decodeURIComponent(parts[parts.length - 1] || "");
}

function renderOverview() {
  const { asset, current, structured_rule } = supportState.payload;
  document.querySelector("#support-title").textContent = asset.name;
  if (supportState.payload.contract) {
    document.querySelector("#support-contract-link").href = `/contracts/${encodeURIComponent(supportState.payload.contract.contract_id)}`;
    document.querySelector("#support-back").href = `/contracts/${encodeURIComponent(supportState.payload.contract.contract_id)}`;
  }
  document.querySelector("#support-overview").innerHTML = `
    <article class="metric">
      <div class="metric-label">Type d’actif</div>
      <div class="metric-value">${esc(asset.asset_type)}</div>
      <div class="metric-sub">${esc(asset.valuation_engine)}</div>
    </article>
    <article class="metric">
      <div class="metric-label">Valeur actuelle</div>
      <div class="metric-value">${current ? euro(current.current_value) : "N/A"}</div>
      <div class="metric-sub">${current ? esc(current.view_name) : "pas de ligne active"}</div>
    </article>
    <article class="metric">
      <div class="metric-label">ISIN</div>
      <div class="metric-value">${esc(asset.isin || "N/A")}</div>
      <div class="metric-sub">${esc(supportState.payload.position.contract_name)}</div>
    </article>
    <article class="metric">
      <div class="metric-label">Règle structurée</div>
      <div class="metric-value">${structured_rule ? esc(structured_rule.rule_status) : "N/A"}</div>
      <div class="metric-sub">${structured_rule && structured_rule.brochure_filename ? esc(structured_rule.brochure_filename) : "aucune brochure liée"}</div>
    </article>
  `;
}

function renderRuleForm() {
  const form = supportState.payload.structured_rule_form;
  const isStructured = supportState.payload.asset.asset_type === "structured_product";
  const formNode = document.querySelector("#structured-rule-form");
  if (!formNode) {
    return;
  }
  const card = formNode.closest(".section-card");
  if (!isStructured) {
    if (card) card.style.display = "none";
    return;
  }
  if (card) card.style.display = "";
  document.querySelector("#rule-display-name").value = form.display_name_override || "";
  document.querySelector("#rule-isin").value = form.isin_override || "";
  document.querySelector("#rule-source-mode").value = form.rule_source_mode || "mixed";
  document.querySelector("#rule-coupon-payment-mode").value = form.coupon_payment_mode || "unknown";
  document.querySelector("#rule-coupon-frequency").value = form.coupon_frequency || "";
  document.querySelector("#rule-coupon-summary").value = form.coupon_rule_summary || "";
  document.querySelector("#rule-autocall-summary").value = form.autocall_rule_summary || "";
  document.querySelector("#rule-capital-summary").value = form.capital_rule_summary || "";
  document.querySelector("#rule-notes").value = form.notes || "";
}

function renderPosition() {
  const position = supportState.payload.position;
  const rows = [
    { label: "Position", value: position.position_id },
    { label: "Contrat", value: position.contract_name },
    { label: "Assureur", value: position.insurer },
    { label: "Souscription", value: position.subscription_date },
    { label: "Montant investi", value: euro(position.invested_amount) },
    { label: "Unités détenues", value: position.units_held ?? "N/A" },
    { label: "Purchase NAV", value: position.purchase_nav ? euro(position.purchase_nav) : "N/A" },
    { label: "Source NAV achat", value: position.purchase_nav_source || "N/A" },
  ];
  document.querySelector("#support-position").innerHTML = tableFromRows(rows, [
    ["label", "Champ"],
    ["value", "Valeur"],
  ]);
}

function renderCurrent() {
  const current = supportState.payload.current;
  if (!current) {
    document.querySelector("#support-current").innerHTML = document.querySelector("#v2-empty").innerHTML;
    return;
  }
  const rows = Object.entries(current)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => ({
      key,
      value: typeof value === "number" && /(value|amount|gain|fees|nav)/i.test(key) ? euro(value) : Array.isArray(value) ? JSON.stringify(value) : String(value),
    }));
  document.querySelector("#support-current").innerHTML = tableFromRows(rows, [
    ["key", "Champ"],
    ["value", "Valeur"],
  ]);
}

function renderStructuredSummary() {
  const summary = supportState.payload.structured_summary;
  if (!summary || supportState.payload.asset.asset_type !== "structured_product") {
    document.querySelector("#support-structured-summary").innerHTML = document.querySelector("#v2-empty").innerHTML;
    document.querySelector("#support-expected-events").innerHTML = document.querySelector("#v2-empty").innerHTML;
    return;
  }

  const rows = [
    { label: "Statut global", value: summary.completeness.rule_status },
    { label: "Brochure", value: summary.has_brochure ? (summary.brochure_document?.original_filename || "Présente") : "Manquante" },
    { label: "Fichier d’événements", value: summary.has_events_file ? summary.events_filename : "Manquant" },
    { label: "ISIN", value: summary.completeness.isin_present ? supportState.payload.asset.isin : "Absent" },
    { label: "Première date attendue", value: summary.first_expected_date || "N/A" },
    { label: "Prochaine date attendue", value: summary.next_expected_date || "N/A" },
    { label: "Échéance", value: summary.maturity_date || "N/A" },
    { label: "Gain par période", value: typeof summary.gain_per_period === "number" ? `${(summary.gain_per_period * 100).toFixed(2)}%` : "N/A" },
    { label: "Sous-jacent", value: summary.underlying || "N/A" },
  ];
  document.querySelector("#support-structured-summary").innerHTML = tableFromRows(rows, [
    ["label", "Champ"],
    ["value", "Valeur", (value, row) => row.label === "Statut global"
      ? (value === "complete"
          ? '<span class="pill ok">Complet</span>'
          : value === "partial"
            ? '<span class="pill warn">Partiel</span>'
            : '<span class="pill bad">Insuffisant</span>')
      : esc(value)],
  ]);

  const expectedRows = (summary.expected_events || []).map((event) => ({
    date: event.date,
    type: event.type,
    description: event.description,
  }));
  document.querySelector("#support-expected-events").innerHTML = tableFromRows(expectedRows, [
    ["date", "Date"],
    ["type", "Type", (value) => `<span class="pill">${esc(value)}</span>`],
    ["description", "Description"],
  ]);
}

function validationOptionsForEvent(eventType, current) {
  const values = eventType.includes("payment")
    ? ["unknown", "paid", "not_paid"]
    : ["unknown", "triggered", "not_triggered"];
  return values
    .map((value) => `<option value="${value}" ${current === value ? "selected" : ""}>${value}</option>`)
    .join("");
}

function renderEventValidations() {
  const summary = supportState.payload.structured_summary;
  const isStructured = supportState.payload.asset.asset_type === "structured_product";
  const host = document.querySelector("#support-event-validations");
  const card = host.closest(".section-card");
  if (!isStructured || !summary) {
    if (card) card.style.display = "none";
    document.querySelector("#support-event-validations").innerHTML = document.querySelector("#v2-empty").innerHTML;
    return;
  }
  if (card) card.style.display = "";
  const rows = (summary.expected_events || []).map((event) => `
    <tr>
      <td>${esc(event.date)}</td>
      <td><span class="pill">${esc(event.type)}</span></td>
      <td>${esc(event.description || "")}</td>
      <td>
        <select class="mini-select event-validation-status" data-event-key="${esc(event.event_key)}">
          ${validationOptionsForEvent(event.type || "", event.validation_status || "unknown")}
        </select>
      </td>
      <td><input class="mini-input event-validation-notes" data-event-key="${esc(event.event_key)}" value="${inputEsc(event.validation_notes || "")}"></td>
      <td><button class="ghost-button inline-button event-validation-save" data-event-key="${esc(event.event_key)}" data-event-type="${esc(event.type || "")}" data-event-date="${esc(event.date || "")}" type="button">Sauver</button></td>
    </tr>
  `).join("");
  document.querySelector("#support-event-validations").innerHTML = rows
    ? `<table><thead><tr><th>Date</th><th>Type</th><th>Description</th><th>Statut</th><th>Notes</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : document.querySelector("#v2-empty").innerHTML;
}

function renderDocuments() {
  document.querySelector("#support-documents").innerHTML = tableFromRows(supportState.payload.documents, [
    ["document_date", "Date"],
    ["document_type", "Type", (value) => `<span class="pill">${esc(value)}</span>`],
    ["original_filename", "Document"],
    ["status", "Statut", (value) => `<span class="pill ok">${esc(value)}</span>`],
    ["document_id", "PDF", (_value, row) => `<a class="ghost-button inline-button" href="/documents/${encodeURIComponent(row.document_id)}">Ouvrir</a>`],
  ]);
}

function renderSnapshots() {
  document.querySelector("#support-snapshots").innerHTML = tableFromRows(supportState.payload.snapshots, [
    ["reference_date", "Réf."],
    ["official_total_value", "Total", (value) => euro(value)],
    ["official_uc_value", "UC (officiel)", (value) => euro(value)],
    ["official_structured_value", "Structurés (officiel)", (value) => euro(value)],
    ["model_structured_value", "Structurés (modèle)", (value) => euro(value)],
    ["structured_model_gap_value", "Écart", (_value, row) => {
      if (typeof row.structured_model_gap_value !== "number") return "N/A";
      const klass = row.structured_model_gap_value >= 0 ? "positive" : "negative";
      return `<span class="${klass}">${euro(row.structured_model_gap_value)}</span>`;
    }],
    ["official_fonds_euro_value", "Fonds euro (officiel)", (value) => euro(value)],
  ]);
}

function renderLots() {
  const lots = supportState.payload.position.lots || [];
  document.querySelector("#support-lots").innerHTML = tableFromRows(lots, [
    ["date", "Date"],
    ["type", "Type"],
    ["units", "Unités"],
    ["net_amount", "Montant net", (value) => typeof value === "number" ? euro(value) : esc(value)],
    ["fees_amount", "Frais", (value) => typeof value === "number" ? euro(value) : esc(value)],
  ]);
}

function render() {
  renderOverview();
  renderRuleForm();
  renderPosition();
  renderStructuredSummary();
  renderEventValidations();
  renderCurrent();
  renderDocuments();
  renderSnapshots();
  renderLots();
}

async function fetchSupport() {
  const response = await fetch(`/api/supports/${encodeURIComponent(positionIdFromPath())}`);
  if (!response.ok) {
    throw new Error(`Erreur support (${response.status})`);
  }
  supportState.payload = await response.json();
  render();
}

async function saveStructuredRule(event) {
  event.preventDefault();
  const payload = {
    display_name_override: document.querySelector("#rule-display-name").value,
    isin_override: document.querySelector("#rule-isin").value,
    rule_source_mode: document.querySelector("#rule-source-mode").value,
    coupon_payment_mode: document.querySelector("#rule-coupon-payment-mode").value,
    coupon_frequency: document.querySelector("#rule-coupon-frequency").value,
    coupon_rule_summary: document.querySelector("#rule-coupon-summary").value,
    autocall_rule_summary: document.querySelector("#rule-autocall-summary").value,
    capital_rule_summary: document.querySelector("#rule-capital-summary").value,
    notes: document.querySelector("#rule-notes").value,
  };
  const response = await fetch(`/api/structured-rules/${encodeURIComponent(supportState.payload.asset.asset_id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Erreur fiche produit (${response.status})`);
  }
  await fetchSupport();
}

async function saveEventValidation(button) {
  const eventKey = button.dataset.eventKey;
  const eventType = button.dataset.eventType;
  const eventDate = button.dataset.eventDate;
  const status = document.querySelector(`.event-validation-status[data-event-key="${CSS.escape(eventKey)}"]`).value;
  const notes = document.querySelector(`.event-validation-notes[data-event-key="${CSS.escape(eventKey)}"]`).value;
  const response = await fetch(`/api/structured-events/${encodeURIComponent(supportState.payload.asset.asset_id)}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_key: eventKey,
      event_type: eventType,
      event_date: eventDate,
      validation_status: status,
      notes,
    }),
  });
  if (!response.ok) {
    throw new Error(`Erreur validation événement (${response.status})`);
  }
  await fetchSupport();
}

const ruleForm = document.querySelector("#structured-rule-form");
if (ruleForm) {
  ruleForm.addEventListener("submit", (event) => {
    withLoader(() => saveStructuredRule(event)).catch((error) => window.alert(error.message));
  });
}

document.addEventListener("click", (event) => {
  if (!(event.target instanceof HTMLElement) || !event.target.matches(".event-validation-save")) return;
  withLoader(() => saveEventValidation(event.target)).catch((error) => window.alert(error.message));
});

withLoader(fetchSupport).catch((error) => window.alert(error.message));
