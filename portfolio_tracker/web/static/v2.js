const v2State = {
  payload: null,
  loading: 0,
  documentFilters: {
    contract: "all",
    type: "all",
  },
};

function v2Euro(value) {
  if (typeof value !== "number") return "N/A";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(value);
}

function v2Percent(value) {
  if (typeof value !== "number") return "N/A";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function v2PercentAnnualized(value) {
  if (typeof value !== "number") return "N/A";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%/an`;
}

function v2AnnualizedReturn(totalReturnPct, startDate, endDate) {
  if (typeof totalReturnPct !== "number" || !startDate || !endDate) return null;
  const start = new Date(startDate);
  const end = new Date(endDate);
  const ms = end - start;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const years = ms / (365.25 * 24 * 60 * 60 * 1000);
  if (years <= 0) return null;
  return ((1 + totalReturnPct / 100) ** (1 / years) - 1) * 100;
}

function v2SignedEuro(value) {
  if (typeof value !== "number") return "N/A";
  return `${value >= 0 ? "+" : "-"}${v2Euro(Math.abs(value))}`;
}

function v2SignClass(value) {
  if (typeof value !== "number") return "";
  return value >= 0 ? "positive" : "negative";
}

function v2Esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function v2Loading(active) {
  const overlay = document.querySelector("#v2-loader");
  if (active) {
    v2State.loading += 1;
    overlay.classList.remove("is-hidden");
    return;
  }
  v2State.loading = Math.max(0, v2State.loading - 1);
  if (v2State.loading === 0) {
    overlay.classList.add("is-hidden");
  }
}

async function v2WithLoader(task) {
  v2Loading(true);
  try {
    return await task();
  } finally {
    v2Loading(false);
  }
}

function renderImportSummary() {
  const summary = v2State.payload.import_summary;
  document.querySelector("#v2-import-summary").innerHTML = `
    <div class="row"><span>Base V2</span><strong>${v2Esc(summary.db_path.split("/").pop())}</strong></div>
    <div class="row"><span>Documents</span><strong>${summary.totals.documents}</strong></div>
    <div class="row"><span>Snapshots</span><strong>${summary.totals.snapshots}</strong></div>
    <div class="row"><span>Synchronisé</span><strong>${v2Esc(summary.imported_at.replace("T", " "))}</strong></div>
  `;
}

function renderOverview() {
  const overview = v2State.payload.overview;
  document.querySelector("#v2-overview").innerHTML = `
    <article class="metric">
      <div class="metric-label">Valeur actuelle</div>
      <div class="metric-value">${v2Euro(overview.current_value)}</div>
      <div class="metric-sub">${overview.active_positions_count} positions actives</div>
    </article>
    <article class="metric">
      <div class="metric-label">Apports externes cumulés</div>
      <div class="metric-value">${v2Euro(overview.external_contributions_total)}</div>
      <div class="metric-sub">${overview.contracts_count} contrats dans le périmètre</div>
    </article>
    <article class="metric">
      <div class="metric-label">Performance simple</div>
      <div class="metric-value ${overview.performance_simple_amount >= 0 ? "positive" : "negative"}">${v2Euro(overview.performance_simple_amount)}</div>
      <div class="metric-sub">${v2Percent(overview.performance_simple_pct)}</div>
    </article>
    <article class="metric">
      <div class="metric-label">Import V2</div>
      <div class="metric-value">${v2State.payload.import_summary.totals.snapshots}</div>
      <div class="metric-sub">snapshots officiels importés</div>
    </article>
  `;
}

function renderContracts() {
  const host = document.querySelector("#v2-contracts");
  const cards = v2State.payload.contracts;
  if (!cards.length) {
    host.innerHTML = document.querySelector("#v2-empty").innerHTML;
    return;
  }
  host.innerHTML = cards.map((card) => {
    const latest = card.latest_snapshot;
    const yearProgressLabel = card.year_progress_reference_date
      ? `Depuis le ${v2Esc(card.year_progress_reference_date)}`
      : "Depuis le dernier officiel";
    const yearProgressClass = typeof card.year_progress_amount === "number"
      ? (card.year_progress_amount >= 0 ? "positive" : "negative")
      : "";
    return `
      <article class="contract-card">
        <span class="eyebrow">${v2Esc(card.insurer)}</span>
        <h3><a class="panel-link" href="/contracts/${encodeURIComponent(card.contract_id)}">${v2Esc(card.contract_name)}</a></h3>
        <div class="contract-meta">
          ${v2Esc(card.holder_type)} · fiscalité ${v2Esc(card.fiscal_applicability)} · ${card.active_positions_count} positions actives
        </div>
        <div class="contract-stats">
          <div class="cs-item">
            <span>Valeur actuelle</span>
            <strong>${v2Euro(card.current_value)}</strong>
          </div>
          <div class="cs-item">
            <span>YTD 31/12${card.ytd_reference_date ? ` ${card.ytd_reference_date.slice(0, 4)}` : ""}</span>
            <strong class="${typeof card.ytd_amount === "number" ? (card.ytd_amount >= 0 ? "positive" : "negative") : ""}">${typeof card.ytd_pct === "number" ? v2Percent(card.ytd_pct) : "N/A"}</strong>
            <small>${typeof card.ytd_amount === "number" ? v2SignedEuro(card.ytd_amount) : ""}</small>
          </div>
          <div class="cs-item">
            <span>Dernier officiel</span>
            <strong>${latest ? v2Euro(latest.official_total_value) : "N/A"}</strong>
            <small class="${yearProgressClass}">${yearProgressLabel}</small>
            ${latest ? `<small class="muted">Officiel au ${v2Esc(latest.reference_date)} · modèle aujourd'hui</small>` : ""}
          </div>
          <div class="cs-item">
            <span>Performance</span>
            <strong class="${card.performance_simple_amount >= 0 ? "positive" : "negative"}">${v2Percent(card.performance_simple_pct)}</strong>
            <small>${v2Euro(card.performance_simple_amount)}</small>
          </div>
          <div class="cs-item">
            <span>Apports</span>
            <strong>${v2Euro(card.external_contributions_total)}</strong>
          </div>
          <div class="cs-item">
            <span>UC officiel</span>
            <strong>${latest ? v2Euro(latest.official_uc_value) : "N/A"}</strong>
          </div>
          <div class="cs-item">
            <span>Structurés officiel</span>
            <strong>${latest ? v2Euro(latest.official_structured_value) : "N/A"}</strong>
          </div>
          <div class="cs-item">
            <span>Structurés modèle</span>
            <strong>${latest ? v2Euro(latest.model_structured_value) : "N/A"}</strong>
            <small class="${latest && typeof latest.structured_model_gap_value === "number" ? (latest.structured_model_gap_value >= 0 ? "positive" : "negative") : ""}">
              ${latest && typeof latest.structured_model_gap_value === "number" ? v2SignedEuro(latest.structured_model_gap_value) : ""}
            </small>
          </div>
          <div class="cs-item">
            <span>Fonds euro officiel</span>
            <strong>${latest ? v2Euro(latest.official_fonds_euro_value) : "N/A"}</strong>
          </div>
          <div class="cs-item">
            <span>Ouverture</span>
            <strong>${card.opening_date ?? "N/A"}</strong>
            <small>${typeof card.months_since_opening === "number" ? `${Math.floor(card.months_since_opening / 12)}a ${card.months_since_opening % 12}m` : ""}</small>
          </div>
          <div class="cs-item cs-item--full">
            ${(() => {
              const r = card.reconciliation;
              if (!r || r.status === "unavailable") return `<span class="pill warning">Aucun snapshot validé — réconciliation indisponible</span>`;
              const klass = r.status === "ok" ? "ok" : "warning";
              const gapText = typeof r.gap_amount === "number" ? ` · écart ${v2SignedEuro(r.gap_amount)}${typeof r.gap_pct === "number" ? ` (${v2Percent(r.gap_pct)})` : ""}` : "";
              return `<span class="pill ${klass}">Réconcilié au ${v2Esc(r.reference_date)}${gapText}</span>`;
            })()}
          </div>
        </div>
        <div style="margin-top: 12px; text-align: right;">
          <a class="primary-button inline-button" href="/contracts/${encodeURIComponent(card.contract_id)}">Gérer ce contrat →</a>
        </div>
      </article>
    `;
  }).join("");
}

function tableFromRows(rows, columns) {
  if (!rows.length) {
    return document.querySelector("#v2-empty").innerHTML;
  }
  const head = columns.map(([, label]) => `<th>${v2Esc(label)}</th>`).join("");
  const body = rows.map((row) => `
    <tr>
      ${columns.map(([key, _label, formatter]) => `<td>${formatter ? formatter(row[key], row) : v2Esc(row[key])}</td>`).join("")}
    </tr>
  `).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderDeltaCell(value, deltaAmount, deltaPct, options = {}) {
  const deltaClass = typeof deltaAmount === "number"
    ? (deltaAmount >= 0 ? "positive" : "negative")
    : "";
  const note = options.note ? `<div class="metric-sub">${v2Esc(options.note)}</div>` : "";
  return `
    <div>${v2Euro(value)}</div>
    <div class="metric-sub ${deltaClass}">
      ${typeof deltaAmount === "number" ? v2Euro(deltaAmount) : "N/A"}
      ${typeof deltaPct === "number" ? ` · ${v2Percent(deltaPct)}` : ""}
    </div>
    ${note}
  `;
}

function renderAmountCell(value) {
  return `<div>${v2Euro(value)}</div>`;
}

function renderGapCell(value, pct) {
  const klass = typeof value === "number" ? (value >= 0 ? "positive" : "negative") : "";
  const pctText = typeof pct === "number" ? v2Percent(pct) : (typeof value === "number" && value !== 0 ? "<em>officiel inclus dans UC</em>" : "");
  return `
    <div class="${klass}">${typeof value === "number" ? v2SignedEuro(value) : "N/A"}</div>
    <div class="metric-sub ${klass}">${pctText}</div>
  `;
}

function renderExternalFlowsCell(_value, row) {
  const summary = row.annual_flow_summary || {};
  const contributions = Number(summary.external_contributions_total || 0);
  const withdrawals = Number(summary.external_withdrawals_total || 0);
  return `
    <div class="metric-sub positive">${v2SignedEuro(contributions)} versements</div>
    <div class="metric-sub ${withdrawals > 0 ? "negative" : ""}">${withdrawals > 0 ? `-${v2Euro(withdrawals)} retraits` : "Aucun retrait"}</div>
  `;
}

function renderFeesTaxesCell(_value, row) {
  const summary = row.annual_flow_summary || {};
  const fees = Number(summary.fees_total || 0);
  const taxes = Number(summary.taxes_total || 0);
  return `
    <div class="metric-sub ${fees > 0 ? "negative" : ""}">${fees > 0 ? `-${v2Euro(fees)} frais` : "Frais nuls"}</div>
    <div class="metric-sub ${taxes > 0 ? "negative" : ""}">${taxes > 0 ? `-${v2Euro(taxes)} taxes` : "Taxes nulles"}</div>
  `;
}

function renderSnapshots() {
  const rows = [];
  Object.entries(v2State.payload.snapshots_by_contract).forEach(([contractName, items]) => {
    items.forEach((item) => {
      rows.push({
        contract_name: contractName,
        reference_date: item.reference_date,
        statement_date: item.statement_date,
        status: item.status,
        official_total_value: item.official_total_value,
        official_uc_value: item.official_uc_value,
        official_structured_value: item.official_structured_value,
        model_structured_value: item.model_structured_value,
        structured_model_gap_value: item.structured_model_gap_value,
        structured_model_gap_pct: item.structured_model_gap_pct,
        official_fonds_euro_value: item.official_fonds_euro_value,
        annual_flow_summary: item.annual_flow_summary || {},
      });
    });
  });
  document.querySelector("#v2-snapshots").innerHTML = tableFromRows(rows, [
    ["contract_name", "Contrat"],
    ["reference_date", "Réf."],
    ["statement_date", "Relevé"],
    ["status", "Statut", (value) => {
      const klass = value === "validated" ? "ok" : value === "rejected" ? "negative" : "warning";
      return `<span class="pill ${klass}">${v2Esc(value)}</span>`;
    }],
    ["official_total_value", "Total fin", (value) => renderAmountCell(value)],
    ["official_uc_value", "UC fin (officiel)", (value) => renderAmountCell(value)],
    ["official_fonds_euro_value", "Fonds euro fin", (value) => renderAmountCell(value)],
    ["official_structured_value", "Structurés fin (officiel)", (value) => renderAmountCell(value)],
    ["model_structured_value", "Structurés fin (modèle)", (value) => renderAmountCell(value)],
    ["structured_model_gap_value", "Écart modèle/officiel", (_value, row) => renderGapCell(row.structured_model_gap_value, row.structured_model_gap_pct)],
    ["annual_flow_summary", "Flux externes année", (_value, row) => renderExternalFlowsCell(_value, row)],
    ["annual_flow_summary", "Crédits constatés", (_value, row) => renderAmountCell(row.annual_flow_summary.credited_income_total || 0)],
    ["annual_flow_summary", "Remb. structurés", (_value, row) => renderAmountCell(row.annual_flow_summary.structured_redemptions_total || 0)],
    ["annual_flow_summary", "Frais / taxes", (_value, row) => renderFeesTaxesCell(_value, row)],
  ]);
}

function renderDocuments() {
  const allRows = v2State.payload.documents;
  const contractSelect = document.querySelector("#v2-documents-contract");
  const typeSelect = document.querySelector("#v2-documents-type");

  const contractOptions = ["all", ...new Set(allRows.map((row) => row.contract_name || "Sans contrat"))];
  const typeOptions = ["all", ...new Set(allRows.map((row) => row.document_type || "unknown"))];

  contractSelect.innerHTML = contractOptions
    .map((value) => `<option value="${v2Esc(value)}">${value === "all" ? "Tous les contrats" : v2Esc(value)}</option>`)
    .join("");
  typeSelect.innerHTML = typeOptions
    .map((value) => `<option value="${v2Esc(value)}">${value === "all" ? "Tous les types" : v2Esc(value)}</option>`)
    .join("");

  contractSelect.value = contractOptions.includes(v2State.documentFilters.contract) ? v2State.documentFilters.contract : "all";
  typeSelect.value = typeOptions.includes(v2State.documentFilters.type) ? v2State.documentFilters.type : "all";

  const rows = allRows.filter((row) => {
    const contractOk = v2State.documentFilters.contract === "all"
      || (row.contract_name || "Sans contrat") === v2State.documentFilters.contract;
    const typeOk = v2State.documentFilters.type === "all"
      || (row.document_type || "unknown") === v2State.documentFilters.type;
    return contractOk && typeOk;
  });

  document.querySelector("#v2-documents").innerHTML = tableFromRows(rows, [
    ["contract_name", "Contrat"],
    ["document_date", "Date"],
    ["document_type", "Type", (value) => `<span class="pill">${v2Esc(value)}</span>`],
    ["original_filename", "Document"],
    ["status", "Statut", (value) => `<span class="pill ok">${v2Esc(value)}</span>`],
    ["document_id", "PDF", (_value, row) => `<a class="ghost-button inline-button" href="/documents/${encodeURIComponent(row.document_id)}">Ouvrir</a>`],
  ]);
}

function renderStructuredCoverage() {
  const rows = v2State.payload.structured_coverage.map((row) => ({
    ...row,
    brochure: row.has_brochure ? row.brochure_filename : "Manquante",
    events: row.has_events_file ? row.events_filename : "Manquant",
  }));
  document.querySelector("#v2-structured").innerHTML = tableFromRows(rows, [
    ["contract_name", "Contrat"],
    ["asset_name", "Produit", (_value, row) => `<a class="table-link" href="/supports/${encodeURIComponent(row.position_id)}">${v2Esc(row.asset_name)}</a>`],
    ["isin", "ISIN"],
    ["brochure", "Brochure", (_value, row) => row.has_brochure ? `<span class="pill ok">${v2Esc(row.brochure)}</span>` : `<span class="pill bad">Manquante</span>`],
    ["events", "Règles calendrier", (_value, row) => row.has_events_file ? `<span class="pill ok">${v2Esc(row.events)}</span>` : `<span class="pill warn">À créer</span>`],
    ["rule_status", "Statut", (value) => (
      value === "complete"
        ? '<span class="pill ok">Complet</span>'
        : value === "partial"
          ? '<span class="pill warn">Partiel</span>'
          : '<span class="pill bad">Insuffisant</span>'
    )],
  ]);
}

function renderStructuredSummary() {
  const sourceRows = (v2State.payload.structured_summary && v2State.payload.structured_summary.length)
    ? v2State.payload.structured_summary
    : ((v2State.payload.views && v2State.payload.views.structured) || []).map((row) => {
      const invested = Number(row.invested_amount || 0);
      const currentValue = Number(row.current_value || 0);
      const gain = currentValue - invested;
      const perf = invested ? (gain / invested) * 100 : 0;
      const perfAnnualized = v2AnnualizedReturn(perf, row.subscription_date, new Date().toISOString().slice(0, 10));
      const perfIfStrike = typeof row.perf_if_strike_next === "number" ? row.perf_if_strike_next : null;
      const perfIfStrikeAnnualized = v2AnnualizedReturn(perfIfStrike, row.subscription_date, row.next_obs);
      let redeemIfToday = "n/a";
      const current = row.underlying_current;
      const threshold = row.redemption_threshold_value;
      const operator = row.redemption_operator;
      if (typeof current === "number" && typeof threshold === "number") {
        if (operator === ">") redeemIfToday = current > threshold ? "OUI" : "non";
        if (operator === ">=") redeemIfToday = current >= threshold ? "OUI" : "non";
        if (operator === "<") redeemIfToday = current < threshold ? "OUI" : "non";
        if (operator === "<=") redeemIfToday = current <= threshold ? "OUI" : "non";
      }
      return {
        position_id: row.position_id,
        name: row.display_name || row.name,
        portfolio_name: row.portfolio_name,
        subscription_date: row.subscription_date,
        months: row.months,
        next_observation_date: row.next_obs,
        redeem_if_today: redeemIfToday,
        coupon_pct: Number(row.coupon_pct || 0),
        invested_amount: invested,
        current_value: currentValue,
        gain,
        perf,
        perf_annualized: perfAnnualized,
        perf_if_strike_annualized: perfIfStrikeAnnualized,
        value_if_strike: Number(row.value_if_strike_next || 0),
        gain_if_strike: Number(row.gain_if_strike_next || 0),
        perf_if_strike: perfIfStrike,
      };
    });

  const rows = sourceRows.map((row) => ({
    ...row,
    redeem_if_today_label: row.redeem_if_today === "OUI"
      ? '<span class="pill ok">OUI</span>'
      : row.redeem_if_today === "non"
        ? '<span class="pill warn">non</span>'
        : '<span class="pill">n/a</span>',
  }));
  document.querySelector("#v2-structured-summary").innerHTML = tableFromRows(rows, [
    ["portfolio_name", "Portefeuille"],
    ["name", "Nom", (_value, row) => row.position_id ? `<a class="table-link" href="/supports/${encodeURIComponent(row.position_id)}">${v2Esc(row.name)}</a>` : v2Esc(row.name)],
    ["subscription_date", "Date achat"],
    ["months", "Mois"],
    ["next_observation_date", "Prochaine"],
    ["redeem_if_today_label", "Remb. si ajd ?", (value) => value],
    ["coupon_pct", "Coupon %", (value) => v2Percent(value).replace("+", "")],
    ["invested_amount", "Achat", (value) => v2Euro(value)],
    ["current_value", "Valeur", (value) => v2Euro(value)],
    ["gain", "Gain", (value) => `<span class="${v2SignClass(value)}">${v2Euro(value)}</span>`],
    ["perf", "Perf", (value) => `<span class="${v2SignClass(value)}">${v2Percent(value)}</span>`],
    ["perf_annualized", "Perf/an", (value) => `<span class="${v2SignClass(value)}">${v2PercentAnnualized(value)}</span>`],
    ["perf_if_strike_annualized", "Perf si strike/an", (value) => `<span class="${v2SignClass(value)}">${v2PercentAnnualized(value)}</span>`],
    ["value_if_strike", "Valeur si strike", (value) => v2Euro(value)],
    ["gain_if_strike", "Gain si strike", (value) => `<span class="${v2SignClass(value)}">${v2Euro(value)}</span>`],
    ["perf_if_strike", "Perf si strike", (value) => `<span class="${v2SignClass(value)}">${v2Percent(value)}</span>`],
  ]);
}

function renderV2() {
  renderImportSummary();
  renderOverview();
  renderContracts();
  renderSnapshots();
  renderDocuments();
  renderStructuredSummary();
  renderStructuredCoverage();
}

async function fetchV2Dashboard() {
  const response = await fetch("/api/dashboard");
  if (!response.ok) {
    throw new Error(`Erreur dashboard v2 (${response.status})`);
  }
  v2State.payload = await response.json();
  renderV2();
}

async function bootstrapV2() {
  const response = await fetch("/api/bootstrap", { method: "POST" });
  if (!response.ok) {
    throw new Error(`Erreur import v2 (${response.status})`);
  }
  await response.json();
  await fetchV2Dashboard();
}

document.querySelector("#v2-bootstrap").addEventListener("click", () => {
  v2WithLoader(bootstrapV2).catch((error) => window.alert(error.message));
});

document.querySelector("#v2-documents-contract").addEventListener("change", (event) => {
  v2State.documentFilters.contract = event.target.value;
  renderDocuments();
});

document.querySelector("#v2-documents-type").addEventListener("change", (event) => {
  v2State.documentFilters.type = event.target.value;
  renderDocuments();
});

v2WithLoader(fetchV2Dashboard).catch((error) => {
  window.alert(error.message);
});
