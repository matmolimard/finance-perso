const marketState = {
  payload: null,
  chart: null,
  loading: 0,
  selected: null,
  seriesOptions: [],
  seriesOptionsAll: [],
  editingUrl: null,
  suppressPeriodSync: false,
};

const purchaseMarkersPlugin = {
  id: "purchaseMarkers",
  afterDatasetsDraw(chart, _args, pluginOptions) {
    const markers = pluginOptions?.markers || [];
    if (!markers.length) return;
    const xScale = chart.scales.x;
    const yScale = chart.scales.y;
    if (!xScale || !yScale) return;
    const { ctx } = chart;
    ctx.save();
    markers.forEach((marker) => {
      const pixelX = xScale.getPixelForValue(marker.date);
      if (!Number.isFinite(pixelX)) return;
      ctx.strokeStyle = marker.color || "#b54708";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(pixelX, yScale.top);
      ctx.lineTo(pixelX, yScale.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = marker.color || "#b54708";
      ctx.font = "12px Helvetica Neue, sans-serif";
      ctx.fillText(marker.label || marker.date, Math.min(pixelX + 6, xScale.right - 90), yScale.top + 14);
    });
    ctx.restore();
  },
};

const thresholdLinesPlugin = {
  id: "thresholdLines",
  afterDatasetsDraw(chart, _args, pluginOptions) {
    const lines = pluginOptions?.lines || [];
    if (!lines.length) return;
    const xScale = chart.scales.x;
    const yScale = chart.scales.y;
    if (!xScale || !yScale) return;
    const { ctx } = chart;
    ctx.save();
    lines.forEach((line, index) => {
      const pixelY = yScale.getPixelForValue(line.level);
      if (!Number.isFinite(pixelY)) return;
      const color = line.color || "#188038";
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([8, 4]);
      ctx.beginPath();
      ctx.moveTo(xScale.left, pixelY);
      ctx.lineTo(xScale.right, pixelY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = "12px Helvetica Neue, sans-serif";
      const label = line.shortLabel || `Remb. ${index + 1}`;
      ctx.fillText(label, xScale.left + 8, Math.max(pixelY - 6, yScale.top + 14));
    });
    ctx.restore();
  },
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function euro(value) {
  if (typeof value !== "number") return "N/A";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(value);
}

function isoDateLocal(input) {
  const year = input.getFullYear();
  const month = String(input.getMonth() + 1).padStart(2, "0");
  const day = String(input.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultRange() {
  const dateTo = new Date();
  const dateFrom = new Date(dateTo);
  dateFrom.setFullYear(dateFrom.getFullYear() - 1);
  return {
    dateFrom: isoDateLocal(dateFrom),
    dateTo: isoDateLocal(dateTo),
  };
}

function parseLocalDate(value) {
  if (!value) return null;
  const parsed = new Date(`${value}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function currentIsoDate() {
  return isoDateLocal(new Date());
}

function isNearZero(value, epsilon = 0.01) {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= epsilon;
}

function classifySeriesLifecycle(option) {
  const purchaseDates = (option.purchase_dates || []).filter(Boolean).sort();
  if (!purchaseDates.length) return "current";
  const today = currentIsoDate();
  const earliestPurchaseDate = purchaseDates[0];
  const holdingAmount = typeof option.holding_amount === "number" ? option.holding_amount : 0;
  if (earliestPurchaseDate > today && isNearZero(holdingAmount)) {
    return "future";
  }
  if (earliestPurchaseDate <= today && isNearZero(holdingAmount)) {
    return "former";
  }
  return "current";
}

function prefixContracts(label, linkedContracts = []) {
  const contracts = (linkedContracts || []).filter(Boolean);
  if (!contracts.length) return label;
  return `${contracts.join(" + ")} · ${label}`;
}

function shiftDateFromPeriod(dateToValue, periodValue, minDateValue = "") {
  const endDate = parseLocalDate(dateToValue);
  if (!endDate) return "";
  if (periodValue === "max") {
    return minDateValue || "";
  }
  if (periodValue === "ytd") {
    return `${endDate.getFullYear()}-01-01`;
  }
  const startDate = new Date(endDate);
  if (periodValue === "1m") startDate.setMonth(startDate.getMonth() - 1);
  if (periodValue === "3m") startDate.setMonth(startDate.getMonth() - 3);
  if (periodValue === "1y") startDate.setFullYear(startDate.getFullYear() - 1);
  if (periodValue === "5y") startDate.setFullYear(startDate.getFullYear() - 5);
  const shifted = isoDateLocal(startDate);
  if (minDateValue && shifted < minDateValue) {
    return minDateValue;
  }
  return shifted;
}

function syncPeriodLabel(isCustom = false) {
  const select = document.querySelector("#market-period-select");
  if (!select) return;
  const customOption = Array.from(select.options).find((option) => option.value === "custom");
  if (customOption) {
    customOption.hidden = !isCustom;
  }
  if (isCustom) {
    select.value = "custom";
  } else if (select.value === "custom") {
    select.value = "1y";
  }
}

function purchaseDateForSelectedSeries() {
  const purchaseDates = (marketState.selected?.meta?.purchase_dates || []).filter(Boolean).sort();
  return purchaseDates[0] || "";
}

function syncPeriodOptions() {
  const select = document.querySelector("#market-period-select");
  if (!select) return;
  const purchaseOption = Array.from(select.options).find((option) => option.value === "purchase");
  if (!purchaseOption) return;
  const purchaseDate = purchaseDateForSelectedSeries();
  purchaseOption.hidden = !purchaseDate;
  if (select.value === "purchase" && !purchaseDate) {
    select.value = "1y";
  }
}

function formatSignedNumber(value, digits = 2) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "N/A";
  const abs = number(Math.abs(value), digits);
  if (value > 0) return `+${abs}`;
  if (value < 0) return `-${abs}`;
  return abs;
}

function formatSignedPercent(value, digits = 3) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "N/A";
  const abs = number(Math.abs(value), digits);
  if (value > 0) return `+${abs}%`;
  if (value < 0) return `-${abs}%`;
  return `${abs}%`;
}

function computeRangePerformance(points) {
  if (!Array.isArray(points) || points.length < 2) {
    return { amount: null, pct: null, firstDate: null, lastDate: null };
  }
  const firstPoint = points[0];
  const lastPoint = points[points.length - 1];
  const amount = lastPoint.value - firstPoint.value;
  const pct = firstPoint.value ? (amount / firstPoint.value) * 100 : null;
  return {
    amount,
    pct,
    firstDate: firstPoint.date,
    lastDate: lastPoint.date,
  };
}

function computeHoldingRangeGain(points, holdings) {
  if (!Array.isArray(points) || points.length < 2 || !Array.isArray(holdings) || !holdings.length) {
    return { amount: null, pct: null, startAmount: null, endAmount: null };
  }
  const lastPoint = points[points.length - 1];
  if (typeof lastPoint?.value !== "number" || !Number.isFinite(lastPoint.value) || lastPoint.value === 0) {
    return { amount: null, pct: null, startAmount: null, endAmount: null };
  }

  let startAmount = 0;
  let endAmount = 0;
  let eligibleHoldings = 0;

  for (const holding of holdings) {
    const currentValue = holding?.current_value;
    if (typeof currentValue !== "number" || !Number.isFinite(currentValue) || currentValue === 0) {
      continue;
    }
    const effectiveStartDate = holding?.subscription_date && holding.subscription_date > points[0].date
      ? holding.subscription_date
      : points[0].date;
    const startPoint = points.find((point) => point.date >= effectiveStartDate);
    if (!startPoint || typeof startPoint.value !== "number" || !Number.isFinite(startPoint.value)) {
      continue;
    }
    if (startPoint.date > lastPoint.date) {
      continue;
    }
    const estimatedStartValue = currentValue * (startPoint.value / lastPoint.value);
    startAmount += estimatedStartValue;
    endAmount += currentValue;
    eligibleHoldings += 1;
  }

  if (!eligibleHoldings) {
    return { amount: null, pct: null, startAmount: null, endAmount: null };
  }

  const amount = endAmount - startAmount;
  const pct = startAmount ? (amount / startAmount) * 100 : null;
  return { amount, pct, startAmount, endAmount };
}

function number(value, digits = 2) {
  if (typeof value !== "number") return "N/A";
  return new Intl.NumberFormat("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
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
    marketState.loading += 1;
    overlay.classList.remove("is-hidden");
    return;
  }
  marketState.loading = Math.max(0, marketState.loading - 1);
  if (marketState.loading === 0) {
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

function renderSummary() {
  const summary = marketState.payload.summary;
  document.querySelector("#market-summary").innerHTML = `
    <article class="metric">
      <div class="metric-label">UC couvertes</div>
      <div class="metric-value">${summary.uc_with_series}/${summary.uc_count}</div>
      <div class="metric-sub">séries NAV disponibles</div>
    </article>
    <article class="metric">
      <div class="metric-label">Sous-jacents couverts</div>
      <div class="metric-value">${summary.underlying_with_series}/${summary.underlying_count}</div>
      <div class="metric-sub">séries disponibles</div>
    </article>
  `;
}

function buildSeriesOptions() {
  const ucOptions = marketState.payload.uc_assets
    .filter((row) => row.has_series)
    .map((row) => ({
      value: `uc::${row.asset_id}`,
      kind: "uc",
      id: row.asset_id,
      label: row.name,
      group: "Unités de compte",
      minDate: row.earliest_date || "",
      maxDate: row.latest_date || "",
      isin: row.isin || null,
      linked_contracts: row.linked_contracts || [],
      purchase_dates: row.purchase_dates || [],
      holding_amount: row.holding_amount || 0,
      holdings: row.holdings || [],
      quantalys_rating: row.quantalys_rating ?? null,
      quantalys_category: row.quantalys_category ?? null,
      quantalys_last_update: row.quantalys_last_update ?? null,
      quantalys_search_url: row.quantalys_search_url || null,
      product_names: [],
      redemption_levels: [],
      lifecycle: "current",
    }));
  const underlyingOptions = marketState.payload.underlyings
    .filter((row) => row.has_series)
    .map((row) => ({
      value: `${row.kind || "underlying"}::${row.underlying_id}`,
      kind: row.kind || "underlying",
      id: row.underlying_id,
      label: row.underlying_id,
      group: "Sous-jacents",
      minDate: row.earliest_date || "",
      maxDate: row.latest_date || "",
      isin: null,
      linked_contracts: row.linked_contracts || [],
      purchase_dates: row.purchase_dates || [],
      holding_amount: row.holding_amount || 0,
      holdings: row.holdings || [],
      quantalys_rating: null,
      quantalys_category: null,
      quantalys_last_update: null,
      quantalys_search_url: null,
      product_names: row.product_names || [],
      redemption_levels: row.redemption_levels || [],
      lifecycle: "current",
    }));
  marketState.seriesOptionsAll = [...ucOptions, ...underlyingOptions].map((option) => ({
    ...option,
    displayLabel: prefixContracts(option.label, option.linked_contracts),
    lifecycle: classifySeriesLifecycle(option),
  }));
  applySeriesFilters();
}

function applySeriesFilters() {
  const includeFormer = document.querySelector("#market-include-former")?.checked ?? true;
  const includeFuture = document.querySelector("#market-include-future")?.checked ?? true;
  marketState.seriesOptions = marketState.seriesOptionsAll.filter((option) => {
    if (option.lifecycle === "former" && !includeFormer) return false;
    if (option.lifecycle === "future" && !includeFuture) return false;
    return true;
  });
}

function renderSeriesSelect() {
  const select = document.querySelector("#market-series-select");
  const previousValue = marketState.selected ? `${marketState.selected.kind}::${marketState.selected.id}` : "";
  const groups = new Map();
  for (const option of marketState.seriesOptions) {
    if (!groups.has(option.group)) {
      groups.set(option.group, []);
    }
    groups.get(option.group).push(option);
  }

  const optionMarkup = ['<option value="">Choisir une série</option>'];
  for (const [group, options] of groups.entries()) {
    optionMarkup.push(`<optgroup label="${esc(group)}">`);
    for (const option of options) {
      optionMarkup.push(`<option value="${esc(option.value)}">${esc(option.displayLabel || option.label)}</option>`);
    }
    optionMarkup.push("</optgroup>");
  }
  select.innerHTML = optionMarkup.join("");

  const fallbackValue = marketState.seriesOptions[0]?.value || "";
  select.value = marketState.seriesOptions.some((option) => option.value === previousValue) ? previousValue : fallbackValue;
  if (select.value) {
    const [kind, id] = select.value.split("::");
    const match = marketState.seriesOptions.find((option) => option.kind === kind && option.id === id);
    if (match) {
      syncSelectedSeries(match.kind, match.id, match.displayLabel || match.label);
    }
  } else {
    marketState.selected = null;
  }
}

function syncSelectedSeries(kind, id, label) {
  const option = marketState.seriesOptions.find((entry) => entry.kind === kind && entry.id === id);
  marketState.selected = { kind, id, label, meta: option || null };
  document.querySelector("#market-series-select").value = `${kind}::${id}`;
  syncPeriodOptions();
}

function applyDefaultRange(maxDate = "") {
  const defaults = defaultRange();
  let end = maxDate || defaults.dateTo;
  if (end > defaults.dateTo) {
    end = defaults.dateTo;
  }
  const minDate = marketState.selected?.meta?.minDate || "";
  syncPeriodOptions();
  document.querySelector("#market-date-from").value = shiftDateFromPeriod(end, "1y", minDate);
  document.querySelector("#market-date-to").value = end;
  syncPeriodLabel(false);
  document.querySelector("#market-period-select").value = "1y";
}

function selectSeries(kind, id, label, _minDate, maxDate, preserveRange = false) {
  syncSelectedSeries(kind, id, label);
  if (!preserveRange) {
    applyDefaultRange(maxDate);
  }
  withLoader(fetchSeries).catch((error) => window.alert(error.message));
}

function renderUrlCell(kind, identifier, url, source) {
  let displayUrl;
  if (url) {
    displayUrl = `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="table-link" title="${esc(url)}">${esc(url.replace(/^https?:\/\//, "").substring(0, 40))}${url.length > 46 ? "…" : ""}</a>`;
  } else if (source) {
    displayUrl = `<span class="pill">${esc(source)}</span>`;
  } else {
    displayUrl = '<span class="pill warn">Aucune</span>';
  }
  return `<div class="url-cell-wrap">
    ${displayUrl}
    <button class="ghost-button inline-button market-url-edit" data-kind="${esc(kind)}" data-id="${esc(identifier)}" data-url="${esc(url || "")}" type="button" title="Modifier l'URL">✏️</button>
  </div>`;
}

function renderAddPointBtn(kind, identifier, label) {
  return `<button class="ghost-button inline-button market-add-point" data-kind="${esc(kind)}" data-id="${esc(identifier)}" data-label="${esc(label)}" type="button">+ Valeur</button>`;
}

function marketUrlModalElements() {
  return {
    shell: document.querySelector("#market-url-modal"),
    copy: document.querySelector("#market-url-modal-copy"),
    input: document.querySelector("#market-url-input"),
    form: document.querySelector("#market-url-form"),
  };
}

function closeMarketUrlModal() {
  const { shell, form } = marketUrlModalElements();
  marketState.editingUrl = null;
  form?.reset();
  shell?.classList.add("is-hidden");
  shell?.setAttribute("aria-hidden", "true");
}

function openMarketUrlModal(kind, identifier, label, url = "") {
  const { shell, copy, input } = marketUrlModalElements();
  marketState.editingUrl = { kind, identifier, label };
  if (copy) {
    copy.textContent = `Renseigne l’URL utilisée pour ${label}.`;
  }
  if (input) {
    input.value = url || "";
  }
  shell?.classList.remove("is-hidden");
  shell?.setAttribute("aria-hidden", "false");
  window.setTimeout(() => input?.focus(), 0);
}

async function saveMarketSourceUrl() {
  const { input } = marketUrlModalElements();
  if (!marketState.editingUrl || !input) return;
  const url = input.value.trim();
  if (url && !/^https?:\/\//i.test(url)) {
    window.alert("L'URL doit commencer par http:// ou https://");
    input.focus();
    return;
  }
  const { kind, identifier } = marketState.editingUrl;
  await withLoader(() =>
    fetch("/api/market/source-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, identifier, url }),
    })
      .then((r) => r.json())
      .then((result) => {
        if (!result.ok) throw new Error(result.error || "Erreur");
        return fetchMarket();
      })
  );
  closeMarketUrlModal();
}

function renderUcTable() {
  const rows = marketState.payload.uc_assets;
  document.querySelector("#market-uc-table").innerHTML = tableFromRows(rows, [
    ["name", "UC"],
    ["latest_date", "Dernière date"],
    ["latest_value", "Dernière VL", (value, row) => row.has_series ? `<button class="ghost-button inline-button market-series-trigger" data-kind="uc" data-id="${esc(row.asset_id)}" data-label="${esc(prefixContracts(row.name, row.linked_contracts || []))}" data-min-date="" data-max-date="${esc(row.latest_date || "")}" type="button">${euro(value)}</button>` : '<span class="pill bad">Manquant</span>'],
    ["points_count", "Points"],
    ["source_url", "URL source", (_value, row) => renderUrlCell("uc", row.asset_id, row.source_url, row.source)],
    ["asset_id", "Saisie", (_value, row) => renderAddPointBtn("uc", row.asset_id, prefixContracts(row.name, row.linked_contracts || []))],
  ]);
}

function renderUnderlyingTable() {
  const rows = marketState.payload.underlyings.map((row) => ({
    ...row,
    products_joined: row.products.join(", "),
  }));
  document.querySelector("#market-underlyings-table").innerHTML = tableFromRows(rows, [
    ["underlying_id", "Sous-jacent"],
    ["products_joined", "Produits"],
    ["latest_date", "Dernière date"],
    ["latest_value", "Dernière valeur", (value, row) => row.has_series ? `<button class="ghost-button inline-button market-series-trigger" data-kind="${esc(row.kind || "underlying")}" data-id="${esc(row.underlying_id)}" data-label="${esc(prefixContracts(row.underlying_id, row.linked_contracts || []))}" data-min-date="" data-max-date="${esc(row.latest_date || "")}" type="button">${typeof value === "number" ? value.toFixed(3) : "N/A"}</button>` : '<span class="pill bad">Manquant</span>'],
    ["points_count", "Points"],
    ["url", "URL source", (_value, row) => renderUrlCell(row.kind || "underlying", row.underlying_id, row.url, row.source)],
    ["underlying_id", "Saisie", (_value, row) => renderAddPointBtn(row.kind || "underlying", row.underlying_id, prefixContracts(row.underlying_id, row.linked_contracts || []))],
  ]);
}

function renderChart(points, label) {
  const canvas = document.querySelector("#market-chart");
  const ctx = canvas.getContext("2d");
  if (marketState.chart) {
    marketState.chart.destroy();
  }
  if (typeof Chart === "undefined") {
    window.alert("Chart.js n'est pas disponible dans ce navigateur.");
    return;
  }
  const thresholdLines = (marketState.selected?.meta?.redemption_levels || []).map((line) => ({
    level: line.level,
    shortLabel: `${line.product_name || "Structuré"} · ${number(line.level, 3)}`,
  }));
  const datasetLabel = marketState.selected?.kind === "underlying" && marketState.selected?.meta?.product_names?.length
    ? `${label} · ${marketState.selected.meta.product_names[0]}`
    : label;
  marketState.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: points.map((point) => point.date),
      datasets: [
        {
          label: datasetLabel,
          data: points.map((point) => point.value),
          borderColor: "#1a73e8",
          backgroundColor: "rgba(26, 115, 232, 0.12)",
          borderWidth: 2,
          tension: 0.18,
          fill: true,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true },
        purchaseMarkers: {
          markers: (marketState.selected?.meta?.purchase_dates || []).map((date, index) => ({
            date,
            label: index === 0 ? "Achat" : `Achat ${index + 1}`,
          })),
        },
        thresholdLines: {
          lines: thresholdLines,
        },
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 10 },
        },
        y: {
          beginAtZero: false,
        },
      },
    },
    plugins: [purchaseMarkersPlugin, thresholdLinesPlugin],
  });
}

function renderSeriesMeta(seriesPayload = null) {
  const host = document.querySelector("#market-series-meta");
  const meta = marketState.selected?.meta;
  if (!meta) {
    host.innerHTML = "";
    return;
  }
  const progress = computeRangePerformance(seriesPayload?.points || []);
  const holdingGain = marketState.selected?.kind === "uc"
    ? computeHoldingRangeGain(seriesPayload?.points || [], meta.holdings || [])
    : { amount: null, pct: null, startAmount: null, endAmount: null };
  const contracts = meta.linked_contracts?.length ? meta.linked_contracts.join(", ") : "N/A";
  const purchaseDates = meta.purchase_dates?.length ? meta.purchase_dates.join(", ") : "N/A";
  const productNames = meta.product_names?.length ? meta.product_names.join(", ") : "";
  const holdings = (meta.holdings || []).map((holding) => `
    <li>
      <strong>${esc(holding.contract_name || holding.product_name || "Support")}</strong>
      ${holding.subscription_date ? ` · achat ${esc(holding.subscription_date)}` : ""}
      ${typeof holding.current_value === "number" ? ` · ${euro(holding.current_value)}` : ""}
      ${holding.support_url ? ` · <a class="table-link" href="${esc(holding.support_url)}">voir la fiche</a>` : ""}
    </li>
  `).join("");
  const links = [
    meta.quantalys_search_url ? `<a class="ghost-button inline-button" href="${esc(meta.quantalys_search_url)}" target="_blank" rel="noopener noreferrer">Recherche Quantalys</a>` : "",
  ].filter(Boolean).join("");

  host.innerHTML = `
    <div class="series-meta-grid">
      <article class="metric">
        <div class="metric-label">Progression période</div>
        <div class="metric-value small">
          ${progress.amount === null ? "N/A" : `${formatSignedNumber(progress.amount, 2)} | ${formatSignedPercent(progress.pct, 3)}`}
        </div>
        ${progress.firstDate && progress.lastDate ? `<div class="metric-sub">Du ${esc(progress.firstDate)} au ${esc(progress.lastDate)}</div>` : '<div class="metric-sub">Pas assez de points sur la plage</div>'}
      </article>
      <article class="metric">
        <div class="metric-label">Contrat lié</div>
        <div class="metric-sub">${esc(contracts)}</div>
      </article>
      <article class="metric">
        <div class="metric-label">Montant détenu</div>
        <div class="metric-value small">${euro(meta.holding_amount)}</div>
        <div class="metric-sub">
          ${holdingGain.amount === null ? "Gain/perte période indisponible" : `Gain/perte période estimé: ${formatSignedNumber(holdingGain.amount, 2)} €${holdingGain.pct === null ? "" : ` | ${formatSignedPercent(holdingGain.pct, 2)}`}`}
        </div>
      </article>
      <article class="metric">
        <div class="metric-label">Date d'achat</div>
        <div class="metric-sub">${esc(purchaseDates)}</div>
      </article>
      <article class="metric">
        <div class="metric-label">Référence</div>
        <div class="metric-sub">${meta.isin ? `ISIN ${esc(meta.isin)}` : esc(meta.id)}</div>
        ${productNames ? `<div class="metric-sub">Structuré: ${esc(productNames)}</div>` : ""}
        ${meta.quantalys_rating ? `<div class="metric-sub">Quantalys: ${"⭐".repeat(meta.quantalys_rating)} (${meta.quantalys_rating}/5)</div>` : ""}
        ${meta.quantalys_category ? `<div class="metric-sub">Catégorie: ${esc(meta.quantalys_category)}</div>` : ""}
      </article>
    </div>
    ${(meta.holdings || []).length ? `<div class="series-meta-detail"><strong>Détention détaillée</strong><ul>${holdings}</ul></div>` : ""}
    ${links ? `<div class="action-row">${links}</div>` : ""}
  `;
}

async function fetchMarket() {
  const response = await fetch("/api/market");
  if (!response.ok) {
    throw new Error(`Erreur données de marché (${response.status})`);
  }
  marketState.payload = await response.json();
  buildSeriesOptions();
  renderSummary();
  renderSeriesSelect();
  renderUcTable();
  renderUnderlyingTable();

  if (!marketState.selected && marketState.seriesOptions.length) {
    const first = marketState.seriesOptions[0];
    syncSelectedSeries(first.kind, first.id, first.label);
    applyDefaultRange(first.maxDate);
    await fetchSeries();
  } else if (marketState.selected) {
    await fetchSeries();
  }
}

async function fetchSeries() {
  if (!marketState.selected) {
    const select = document.querySelector("#market-series-select");
    if (select.value) {
      const [kind, id] = select.value.split("::");
      const match = marketState.seriesOptions.find((option) => option.kind === kind && option.id === id);
      if (match) {
        syncSelectedSeries(match.kind, match.id, match.label);
      }
    }
  }
  if (!marketState.selected) return;
  const params = new URLSearchParams({
    kind: marketState.selected.kind,
    id: marketState.selected.id,
  });
  const dateFrom = document.querySelector("#market-date-from").value;
  const dateTo = document.querySelector("#market-date-to").value;
  const period = document.querySelector("#market-period-select").value;
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);

  const response = await fetch(`/api/market/series?${params.toString()}`);
  if (!response.ok) {
    throw new Error(`Erreur série (${response.status})`);
  }
  const payload = await response.json();
  if (period === "max" && !dateFrom && payload.points.length) {
    document.querySelector("#market-date-from").value = payload.points[0].date;
  }
  renderSeriesMeta(payload);
  renderChart(payload.points, marketState.selected.label);
}

function applyPeriodSelection({ fetchAfter = true } = {}) {
  if (marketState.suppressPeriodSync) return;
  const period = document.querySelector("#market-period-select").value;
  if (!period || period === "custom") return;
  const today = defaultRange().dateTo;
  const selectedMaxDate = marketState.selected?.meta?.maxDate || today;
  const currentDateTo = document.querySelector("#market-date-to").value || selectedMaxDate;
  const boundedDateTo = currentDateTo > today ? today : currentDateTo;
  const minDate = marketState.selected?.meta?.minDate || "";
  const purchaseDate = purchaseDateForSelectedSeries();
  document.querySelector("#market-date-to").value = boundedDateTo;
  document.querySelector("#market-date-from").value = period === "purchase"
    ? (purchaseDate ? (purchaseDate > boundedDateTo ? boundedDateTo : purchaseDate) : shiftDateFromPeriod(boundedDateTo, "1y", minDate))
    : shiftDateFromPeriod(boundedDateTo, period, minDate);
  syncPeriodLabel(false);
  if (fetchAfter) {
    withLoader(fetchSeries).catch((error) => window.alert(error.message));
  }
}

async function triggerAction(url, body = null) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  const payload = await response.json();
  document.querySelector("#market-action-output").textContent = payload.output || payload.error || "";
  if (!response.ok) {
    throw new Error(payload.error || `Erreur action (${response.status})`);
  }
  await fetchMarket();
}

document.querySelector("#market-update-uc").addEventListener("click", () => {
  withLoader(() => triggerAction("/api/market/actions/update-uc-navs")).catch((error) => window.alert(error.message));
});

document.querySelector("#market-update-underlyings").addEventListener("click", () => {
  withLoader(() => triggerAction("/api/market/actions/update-underlyings")).catch((error) => window.alert(error.message));
});

document.querySelector("#market-backfill").addEventListener("click", () => {
  withLoader(() => triggerAction("/api/market/actions/backfill")).catch((error) => window.alert(error.message));
});

document.querySelector("#market-apply-range").addEventListener("click", () => {
  withLoader(fetchSeries).catch((error) => window.alert(error.message));
});

document.querySelector("#market-period-select").addEventListener("change", () => {
  applyPeriodSelection();
});

document.querySelector("#market-date-to").addEventListener("change", () => {
  const period = document.querySelector("#market-period-select").value;
  if (period && period !== "custom") {
    applyPeriodSelection();
    return;
  }
  withLoader(fetchSeries).catch((error) => window.alert(error.message));
});

document.querySelector("#market-date-from").addEventListener("change", () => {
  marketState.suppressPeriodSync = true;
  syncPeriodLabel(true);
  marketState.suppressPeriodSync = false;
  withLoader(fetchSeries).catch((error) => window.alert(error.message));
});

document.querySelector("#market-series-select").addEventListener("change", (event) => {
  const value = event.target.value;
  if (!value) {
    marketState.selected = null;
    return;
  }
  const [kind, id] = value.split("::");
  const match = marketState.seriesOptions.find((option) => option.kind === kind && option.id === id);
  if (!match) return;
  selectSeries(match.kind, match.id, match.displayLabel || match.label, "", match.maxDate);
});

document.querySelector("#market-include-former").addEventListener("change", () => {
  applySeriesFilters();
  renderSeriesSelect();
});

document.querySelector("#market-include-future").addEventListener("change", () => {
  applySeriesFilters();
  renderSeriesSelect();
});

document.addEventListener("click", (event) => {
  if (!(event.target instanceof HTMLElement) || !event.target.matches(".market-series-trigger")) return;
  selectSeries(
    event.target.dataset.kind,
    event.target.dataset.id,
    event.target.dataset.label,
    event.target.dataset.minDate,
    event.target.dataset.maxDate,
  );
});

document.addEventListener("click", (event) => {
  if (!(event.target instanceof HTMLElement)) return;

  if (event.target.matches(".market-url-edit")) {
    const { kind, id, url } = event.target.dataset;
    const label = event.target.closest("tr")?.querySelector("td")?.textContent?.trim() || id;
    openMarketUrlModal(kind, id, label, url || "");
  }

  if (event.target.matches("#market-url-cancel, #market-url-cancel-top, [data-close-market-url-modal='true']")) {
    closeMarketUrlModal();
  }

  if (event.target.matches(".market-add-point")) {
    const { kind, id, label } = event.target.dataset;
    const today = isoDateLocal(new Date());
    const dateStr = window.prompt(`Date (YYYY-MM-DD) pour ${label} :`, today);
    if (!dateStr) return;
    const valueStr = window.prompt(`Valeur pour ${label} au ${dateStr} :`, "");
    if (!valueStr) return;
    const value = parseFloat(valueStr.replace(",", "."));
    if (!Number.isFinite(value)) { window.alert("Valeur invalide"); return; }
    withLoader(() =>
      fetch("/api/market/manual-point", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, identifier: id, date: dateStr, value }),
      })
        .then((r) => r.json())
        .then((result) => {
          if (!result.ok) throw new Error(result.error || "Erreur");
          return fetchMarket();
        })
    ).catch((error) => window.alert(error.message));
  }
});

document.querySelector("#market-url-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  saveMarketSourceUrl().catch((error) => window.alert(error.message));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.querySelector("#market-url-modal")?.classList.contains("is-hidden")) {
    closeMarketUrlModal();
  }
});

withLoader(fetchMarket).catch((error) => window.alert(error.message));
