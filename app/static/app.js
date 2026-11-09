/* global fetch, FormData */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  analysis: null,
  filter: "all",
  selectedId: null,
  emailId: null,
};

const PERIOD = {
  weekly: { short: "wk", long: "per week" },
  biweekly: { short: "2wk", long: "every 2 weeks" },
  monthly: { short: "mo", long: "per month" },
  quarterly: { short: "qtr", long: "per quarter" },
  yearly: { short: "yr", long: "per year" },
};

const DECISION_LABEL = {
  unreviewed: "Not reviewed",
  review: "Needs a look",
  cancel: "Cancelling",
  keep: "Keeping",
};

const USAGE_LABEL = {
  daily: "I use it daily",
  sometimes: "Sometimes",
  rarely: "Rarely",
  never: "Never",
};

const moneyFormatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const compactFormatter = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 0,
});

function money(value) {
  return moneyFormatter.format(Number(value) || 0);
}

function number(value) {
  return compactFormatter.format(Number(value) || 0);
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function parseDate(value) {
  if (!value) return null;
  const [year, month, day] = String(value).slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function shortDate(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function longDate(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function dateParts(value) {
  const date = parseDate(value);
  if (!date) return { day: "—", month: "" };
  return {
    day: date.toLocaleDateString("en-IN", { day: "numeric" }),
    month: date.toLocaleDateString("en-IN", { month: "short" }),
  };
}

function daysLabel(days) {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 0) return `${Math.abs(days)} days ago`;
  return `in ${days} days`;
}

function auditLabel(value) {
  if (!value) return "just now";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "just now";
  const diff = Date.now() - date.getTime();
  if (diff < 60 * 60 * 1000) return "just now";
  return `audited ${shortDate(value)}`;
}

function decisionClass(decision) {
  return ["keep", "review", "cancel"].includes(decision) ? decision : "";
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.error || `Request failed (${response.status})`);
  }
  return payload;
}

function showToast(message, tone = "success") {
  const toast = $("#toast");
  const icon = $(".toast-icon", toast);
  $("#toast-message").textContent = message;
  icon.style.background = tone === "error" ? "var(--coral)" : "var(--lime)";
  toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 4000);
}

function showProcessing(title, sub) {
  $("#processing-title").textContent = title;
  $("#processing-sub").textContent = sub;
  $("#processing-overlay").hidden = false;
}

function hideProcessing() {
  $("#processing-overlay").hidden = true;
}

function showError(message) {
  $("#loading-state").hidden = true;
  $("#dashboard").hidden = true;
  $("#error-state").hidden = false;
  $("#error-message").textContent = message;
}

function setRefreshLoading(isLoading) {
  const button = $("#refresh-ai");
  const label = $("span", button);
  if (isLoading && !button.dataset.previous) {
    button.dataset.previous = button.innerHTML;
  }
  button.disabled = isLoading;
  label.textContent = isLoading ? "Reading your stack…" : "Run live AI brief";
  if (!isLoading && button.dataset.previous) {
    button.innerHTML = button.dataset.previous;
    delete button.dataset.previous;
  }
}

function render() {
  const analysis = state.analysis;
  if (!analysis) return;
  const insights = analysis.insights || {};
  const metrics = analysis.metrics;

  $("#loading-state").hidden = true;
  $("#error-state").hidden = true;
  $("#dashboard").hidden = false;

  $("#data-chip-text").textContent = analysis.is_demo ? "Demo data" : (analysis.source || "Your CSV");
  $("#source-label").textContent = analysis.is_demo ? "Demo transaction audit" : `Audit of ${analysis.source}`;
  $("#audit-date").textContent = auditLabel(analysis.created_at);

  $("#hero-annual").textContent = money(metrics.annual_total);
  $("#hero-sub").textContent =
    `Sift found ${metrics.active_count} recurring charges hiding in ${analysis.transactions_count} transactions. ` +
    `That's ${money(metrics.monthly_total)} a month before you've decided anything.`;

  const flagged = asArray(insights.recommendations).filter((item) => item.action !== "keep").length;
  $("#hero-spot").textContent = flagged ? `${flagged} thing${flagged === 1 ? "" : "s"}` : "no leaks";

  $("#stat-monthly").textContent = money(metrics.monthly_total);
  $("#stat-monthly-note").textContent = `across ${metrics.active_count} detected plans`;
  $("#stat-upcoming").textContent = money(metrics.upcoming_30_total);
  $("#stat-upcoming-note").textContent = `from ${metrics.upcoming_count} renewal${metrics.upcoming_count === 1 ? "" : "s"}`;
  $("#stat-count").textContent = number(metrics.active_count);
  $("#stat-count-note").textContent = `in ${analysis.transactions_count} transactions`;
  $("#stat-hikes").textContent = number(metrics.price_hike_count);
  $("#stat-hikes-note").textContent = metrics.price_hike_count
    ? `+${money(metrics.price_hike_annual_impact)}/yr at the current rhythm`
    : "no price changes detected";

  $("#count-all").textContent = metrics.active_count;
  const needsLook = analysis.subscriptions.filter((sub) => sub.decision === "unreviewed" || sub.decision === "review").length;
  const cancelling = analysis.subscriptions.filter((sub) => sub.decision === "cancel").length;
  $("#count-review").textContent = needsLook;
  $("#count-cancel").textContent = cancelling;

  renderReceipt(analysis);
  renderPlan(analysis);
  renderCategoryBars(analysis);
  renderSubscriptions(analysis);
  renderBrief(analysis);
  renderTimeline(analysis);
}

function renderReceipt(analysis) {
  const subs = [...analysis.subscriptions].sort((a, b) => b.annual_cost - a.annual_cost);
  const lines = subs.slice(0, 4).map((sub) => `
    <div class="receipt-line">
      <span>
        <strong>${escapeHTML(sub.merchant)}</strong>
        <small>${escapeHTML(sub.cadence_label)}</small>
      </span>
      <span>${money(sub.amount)}</span>
    </div>
  `);
  if (subs.length > 4) {
    lines.push(`
      <div class="receipt-line">
        <span><strong>+ ${subs.length - 4} more</strong><small>recurring charges</small></span>
        <span>…</span>
      </div>
    `);
  }
  $("#receipt-lines").innerHTML = lines.join("");
  $("#receipt-total").textContent = money(analysis.metrics.annual_total);
}

function renderPlan(analysis) {
  const plan = analysis.plan || {};
  const decisions = (plan.keeping || 0) + (plan.reviewing || 0) + (plan.cancelling || 0);
  const strip = $("#plan-strip");
  strip.classList.toggle("has-plan", decisions > 0);
  if (!decisions) {
    $("#plan-title").textContent = "Nothing decided yet";
    $("#plan-sub").textContent = "Open a subscription to mark it keep, review or cancel.";
  } else {
    $("#plan-title").textContent = `${decisions} of ${analysis.subscriptions.length} subscription${analysis.subscriptions.length === 1 ? "" : "s"} reviewed.`;
    $("#plan-sub").textContent = [
      plan.cancelling ? `${plan.cancelling} to cancel` : "",
      plan.keeping ? `${plan.keeping} to keep` : "",
      plan.reviewing ? `${plan.reviewing} to review` : "",
    ].filter(Boolean).join(" · ") || "A decision is recorded.";
  }
  $("#plan-saving").innerHTML = `${money(plan.confirmed_annual_savings || 0)}<span>/yr</span>`;
}

function renderCategoryBars(analysis) {
  const groups = new Map();
  analysis.subscriptions.forEach((sub) => {
    const current = groups.get(sub.category) || { total: 0, color: sub.color };
    current.total += sub.monthly_cost;
    groups.set(sub.category, current);
  });
  const entries = [...groups.entries()].sort((a, b) => b[1].total - a[1].total);
  const max = Math.max(...entries.map(([, value]) => value.total), 1);
  $("#category-bars").innerHTML = entries.map(([category, value]) => `
    <div class="category-row">
      <span>${escapeHTML(category)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, (value.total / max) * 100)}%;--bar-color:${value.color}"></div></div>
      <strong>${money(value.total)}/mo</strong>
    </div>
  `).join("");
}

function renderSubscriptions(analysis) {
  const filtered = analysis.subscriptions.filter((sub) => {
    if (state.filter === "review") return sub.decision === "unreviewed" || sub.decision === "review";
    if (state.filter === "cancel") return sub.decision === "cancel";
    return true;
  });
  $("#filter-empty").hidden = filtered.length > 0;
  $("#subscription-grid").hidden = filtered.length === 0;

  $$(".filter-tab").forEach((tab) => {
    const active = tab.dataset.filter === state.filter;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });

  $("#subscription-grid").innerHTML = filtered.map((sub) => {
    const period = PERIOD[sub.cadence] || PERIOD.monthly;
    const priceNote = sub.price_increase
      ? `<span class="price-badge"><svg><use href="#i-trend"/></svg>+${sub.price_increase.percent}%</span>`
      : "";
    return `
      <button class="sub-card ${decisionClass(sub.decision) ? `is-${decisionClass(sub.decision)}` : ""}"
        type="button" data-id="${escapeHTML(sub.id)}" style="--card-color:${sub.color}"
        aria-label="Open ${escapeHTML(sub.merchant)} details">
        <div class="sub-card-top">
          <span class="merchant-logo">${escapeHTML(sub.initials)}</span>
          <span class="confidence ${sub.confidence_label === "Medium" ? "medium" : ""}">${escapeHTML(sub.confidence_label)} confidence</span>
        </div>
        <h3>${escapeHTML(sub.merchant)}</h3>
        <div class="sub-meta-line">${escapeHTML(sub.category)} · ${escapeHTML(sub.cadence_label)} ${priceNote}</div>
        <div class="sub-amount"><strong>${money(sub.amount)}</strong><span>/${period.short}</span></div>
        <div class="sub-card-foot">
          <span>Next ${shortDate(sub.next_renewal)}</span>
          <strong>${money(sub.annual_cost)}/yr</strong>
        </div>
        <span class="decision-pill ${decisionClass(sub.decision)}">${DECISION_LABEL[sub.decision] || "Not reviewed"}</span>
      </button>
    `;
  }).join("");
}

function recommendationFor(id) {
  return asArray(state.analysis?.insights?.recommendations).find((item) => item.id === id);
}

function renderBrief(analysis) {
  const insights = analysis.insights || {};
  const isLive = insights.mode === "live";
  $("#ai-mode").textContent = isLive ? "LIVE" : "LOCAL";
  $("#ai-mode").classList.toggle("local", !isLive);
  const modelName = insights.model && insights.model !== "auto" ? insights.model : "free community model";
  $("#ai-provider").textContent = isLive
    ? `Live read via ${modelName} · only the subscription summary was shared`
    : "Transparent local logic · no data leaves this device";

  $("#ai-headline").textContent = insights.headline || "A grounded first read of your stack.";
  $("#ai-summary").textContent = insights.summary || "Sift is still reading the pattern.";
  const recommendations = asArray(insights.recommendations);
  $("#ai-recommendations").innerHTML = recommendations.length
    ? recommendations.map((item) => `
      <button class="ai-rec" type="button" data-id="${escapeHTML(item.id)}">
        <span class="ai-rec-top">
          <span class="ai-rec-merchant">${escapeHTML(item.merchant)} <svg><use href="#i-arrow"/></svg></span>
          <span class="action-pill ${escapeHTML(item.action)}">${escapeHTML(item.action)}</span>
        </span>
        <p>${escapeHTML(item.reason)}</p>
      </button>
    `).join("")
    : `<p class="ai-summary">No specific flags. Your stack is either beautifully intentional or the model needs more usage context.</p>`;

  const watchouts = asArray(insights.watchouts);
  $("#ai-watchouts").innerHTML = watchouts.map((item) => `<span class="watchout">${escapeHTML(item)}</span>`).join("");

  const reviewValue = (Number(insights.review_annual_savings) || 0) + (Number(insights.potential_annual_savings) || 0);
  $("#ai-review-value").innerHTML = `${money(reviewValue)}<span>/yr</span>`;
  const confirmed = Number(analysis.plan?.confirmed_annual_savings) || 0;
  $("#ai-saving-note").textContent = confirmed
    ? `${money(confirmed)}/yr is confirmed by your cancel decisions.`
    : "Sift can see the charge. You decide if it earned it.";

  const refresh = $("#refresh-ai");
  const label = $("span", refresh);
  if (!refresh.disabled) label.textContent = isLive ? "Refresh live AI brief" : "Run live AI brief";
}

function renderTimeline(analysis) {
  const upcoming = asArray(analysis.metrics.next_30);
  $("#upcoming-total").textContent = money(analysis.metrics.upcoming_30_total);
  if (!upcoming.length) {
    $("#timeline").innerHTML = `<div class="timeline-empty">No renewals in the next 30 days. Quiet month.</div>`;
    return;
  }
  $("#timeline").innerHTML = upcoming.map((item) => {
    const date = dateParts(item.date);
    return `
      <button class="timeline-item" type="button" data-id="${escapeHTML(item.id)}" aria-label="Open ${escapeHTML(item.merchant)}">
        <span class="timeline-date"><strong>${escapeHTML(date.day)}</strong><span>${escapeHTML(date.month)}</span></span>
        <span class="timeline-copy">
          <strong>${escapeHTML(item.merchant)}</strong>
          <span>${daysLabel(item.days)} · ${money(item.amount)}</span>
        </span>
        <span class="timeline-amount">${money(item.amount)}</span>
      </button>
    `;
  }).join("");
}

function renderDetail() {
  const sub = state.analysis?.subscriptions.find((item) => item.id === state.selectedId);
  if (!sub) return;
  const period = PERIOD[sub.cadence] || PERIOD.monthly;
  const rec = recommendationFor(sub.id);
  const charges = asArray(sub.charges).slice(-8);
  const maxCharge = Math.max(...charges.map((charge) => charge.amount), 1);

  const history = charges.map((charge) => `
    <div class="history-col">
      <div class="history-bar"
        style="height:${Math.max(8, (charge.amount / maxCharge) * 88)}%;--card-color:${sub.color}"
        title="${money(charge.amount)} on ${longDate(charge.date)}"></div>
      <span>${parseDate(charge.date)?.toLocaleDateString("en-IN", { month: "short" })}</span>
    </div>
  `).join("");

  const priceAlert = sub.price_increase ? `
    <div class="price-alert">
      <svg><use href="#i-trend"/></svg>
      <span>Price rose from ${money(sub.price_increase.from)} to ${money(sub.price_increase.to)}
      (+${sub.price_increase.percent}%). At this rhythm that is about
      ${money(sub.price_increase.annual_impact)} more per year.</span>
    </div>
  ` : "";

  $("#detail-content").innerHTML = `
    <div class="detail-wrap">
      <div class="detail-top">
        <div class="detail-identity">
          <span class="merchant-logo" style="--card-color:${sub.color}">${escapeHTML(sub.initials)}</span>
          <div>
            <h2 id="detail-title">${escapeHTML(sub.merchant)}</h2>
            <p>${escapeHTML(sub.category)} · detected from ${sub.charge_count} charges</p>
          </div>
        </div>
        <button class="icon-button" type="button" data-close-detail aria-label="Close details"><svg><use href="#i-close"/></svg></button>
      </div>

      <div class="detail-amount-row">
        <div>
          <span>Per charge</span>
          <strong>${money(sub.amount)}</strong>
          <small>${escapeHTML(period.long)} · next ${shortDate(sub.next_renewal)}</small>
        </div>
        <div class="annual">
          <span>Annualised</span>
          <strong>${money(sub.annual_cost)}</strong>
          <small>${money(sub.monthly_cost)} / month</small>
        </div>
      </div>

      <section class="detail-section">
        <h3>The rhythm</h3>
        <div class="detail-facts">
          <div class="fact"><span>Cadence</span><strong>${escapeHTML(sub.cadence_label)}</strong></div>
          <div class="fact"><span>Charges seen</span><strong>${sub.charge_count} times</strong></div>
          <div class="fact">
            <span>Detection</span><strong>${escapeHTML(sub.confidence_label)} · ${Math.round(sub.confidence * 100)}%</strong>
            <div class="confidence-track"><span style="width:${Math.round(sub.confidence * 100)}%"></span></div>
          </div>
        </div>
        <div class="history-chart" aria-label="Charge history">${history}</div>
        ${priceAlert}
      </section>

      <section class="detail-section">
        <h3>Is it still earning its place?</h3>
        <div class="usage-group">
          ${Object.entries(USAGE_LABEL).map(([value, label]) => `
            <button class="choice-button ${sub.usage === value ? "is-active" : ""}" type="button"
              data-usage="${value}" aria-pressed="${sub.usage === value}">${escapeHTML(label)}</button>
          `).join("")}
        </div>
        <p class="detail-note">The detector can see the charge, not the habit. Your daily / sometimes / never signal is what makes the recommendation more useful.</p>
      </section>

      <section class="detail-section">
        <h3>Your call</h3>
        <div class="decision-group">
          <button class="choice-button ${sub.decision === "keep" ? "is-active" : ""}" type="button" data-decision="keep" aria-pressed="${sub.decision === "keep"}">Keep it</button>
          <button class="choice-button ${sub.decision === "review" ? "is-active" : ""}" type="button" data-decision="review" aria-pressed="${sub.decision === "review"}">Review</button>
          <button class="choice-button ${sub.decision === "cancel" ? "is-active" : ""}" type="button" data-decision="cancel" aria-pressed="${sub.decision === "cancel"}">Cancel</button>
        </div>
        <p class="detail-note">Nothing is cancelled, charged or sent automatically. Sift just keeps score.</p>
      </section>

      ${rec ? `
        <div class="ai-note">
          <div class="ai-note-head"><svg><use href="#i-spark"/></svg> Sift AI · ${escapeHTML(rec.action)}</div>
          <p>${escapeHTML(rec.reason)}</p>
        </div>
      ` : ""}

      <div class="detail-actions">
        <button class="button button-dark" type="button" data-email>
          <svg><use href="#i-mail"/></svg> ${sub.decision === "cancel" ? "Draft cancellation email" : "Draft cancellation anyway"}
        </button>
        <button class="text-button" type="button" data-close-detail>Close</button>
      </div>
    </div>
  `;
}

function openDetail(id) {
  state.selectedId = id;
  renderDetail();
  const dialog = $("#detail-dialog");
  if (!dialog.open) dialog.showModal();
}

async function updateDecision(id, decision, usage) {
  const current = state.analysis?.subscriptions.find((item) => item.id === id);
  if (!current) return;
  const body = { id, decision: decision === undefined ? current.decision : decision };
  if (usage !== undefined) body.usage = usage;
  try {
    const data = await fetchJSON("/api/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.analysis = data.analysis;
    const scroll = $("#detail-dialog").scrollTop;
    render();
    if (state.selectedId) {
      renderDetail();
      $("#detail-dialog").scrollTop = scroll;
    }
    if (decision === "cancel") {
      showToast(`${current.merchant} moved to cancelling. Nothing has been sent.`);
    } else if (usage === "never") {
      showToast(`${current.merchant} marked never used. It's a candidate for cancellation.`);
    } else {
      showToast("Decision saved.");
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

function openUpload() {
  const dialog = $("#upload-dialog");
  $("#upload-error").hidden = true;
  if (!dialog.open) dialog.showModal();
}

function setSelectedFile(file) {
  const error = $("#upload-error");
  error.hidden = true;
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".csv")) {
    error.textContent = "That isn't a CSV. Export transactions as CSV or use the sample file.";
    error.hidden = false;
    return;
  }
  if (file.size > 2 * 1024 * 1024) {
    error.textContent = "That file is over 2 MB. Try a shorter date range.";
    error.hidden = false;
    return;
  }
  setSelectedFile.current = file;
  const dropzone = $("#dropzone");
  dropzone.classList.add("has-file");
  $("#dropzone-title").textContent = file.name;
  $("#dropzone-sub").textContent = `${Math.max(1, Math.round(file.size / 1024))} KB · ready to analyse`;
  $("#analyze-button").disabled = false;
}

async function submitUpload(event) {
  event.preventDefault();
  const file = setSelectedFile.current;
  if (!file) {
    const error = $("#upload-error");
    error.textContent = "Choose a CSV first, or load the demo data.";
    error.hidden = false;
    return;
  }
  const button = $("#analyze-button");
  const label = $("span", button);
  button.disabled = true;
  label.textContent = "Analysing…";
  showProcessing("Reading your statement…", "Finding the rhythm, not just the names.");
  try {
    const form = new FormData();
    form.append("file", file);
    const data = await fetchJSON("/api/analyze", { method: "POST", body: form });
    state.analysis = data.analysis;
    state.filter = "all";
    state.selectedId = null;
    setSelectedFile.current = null;
    $("#upload-dialog").close();
    resetUpload();
    render();
    showToast(`Found ${data.analysis.subscriptions.length} subscriptions across ${data.analysis.transactions_count} transactions.`);
    $("#stack").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#upload-error").textContent = error.message;
    $("#upload-error").hidden = false;
  } finally {
    hideProcessing();
    button.disabled = false;
    label.textContent = "Analyse CSV";
    $("#analyze-button").disabled = !setSelectedFile.current;
  }
}

function resetUpload() {
  setSelectedFile.current = null;
  $("#file-input").value = "";
  $("#dropzone").classList.remove("has-file");
  $("#dropzone-title").textContent = "Drop your bank or UPI CSV here";
  $("#dropzone-sub").textContent = "or click to choose a file · up to 2 MB";
  $("#analyze-button").disabled = true;
  $("#upload-error").hidden = true;
}

async function loadDemo() {
  showProcessing("Loading the demo statement…", "A realistic Indian subscription stack, ready to explore.");
  try {
    const data = await fetchJSON("/api/demo", { method: "POST" });
    state.analysis = data.analysis;
    state.filter = "all";
    state.selectedId = null;
    $("#upload-dialog").close();
    resetUpload();
    render();
    showToast("Demo data loaded. Try marking a subscription to review.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    hideProcessing();
  }
}

async function refreshBrief() {
  if (!state.analysis) return;
  setRefreshLoading(true);
  try {
    const data = await fetchJSON("/api/insights", { method: "POST" });
    state.analysis = data.analysis;
    render();
    if (data.analysis.insights?.mode === "live") {
      showToast("Live AI brief ready. The model only saw the subscription summary.");
    } else {
      showToast("No live AI available — the transparent local coach answered instead.");
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setRefreshLoading(false);
    if (state.analysis) renderBrief(state.analysis);
  }
}

async function draftEmail(reason = "") {
  if (!state.emailId) return;
  $("#email-loading").hidden = false;
  $("#email-fields").hidden = true;
  $("#email-provider").textContent = "Drafting…";
  try {
    const data = await fetchJSON("/api/email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.emailId, reason }),
    });
    $("#email-subject").value = data.email.subject;
    $("#email-body").value = data.email.body;
    $("#email-provider").textContent = data.email.mode === "live"
      ? `Drafted by ${data.email.provider} · check every detail`
      : "Local template · edit before sending";
    $("#email-loading").hidden = true;
    $("#email-fields").hidden = false;
    if (data.email.fallback_reason) {
      $("#email-provider").textContent = "Local template used · live AI was unavailable";
    }
  } catch (error) {
    $("#email-loading").hidden = true;
    $("#email-fields").hidden = false;
    $("#email-provider").textContent = "Draft failed — try again";
    showToast(error.message, "error");
  }
}

function openEmailDialog(id) {
  state.emailId = id;
  $("#detail-dialog").close();
  $("#email-reason").value = "";
  $("#reason-wrap").hidden = true;
  $("#email-subject").value = "";
  $("#email-body").value = "";
  $("#email-dialog").showModal();
  draftEmail("");
}

async function copyEmail() {
  const subject = $("#email-subject").value;
  const body = $("#email-body").value;
  const text = `Subject: ${subject}\n\n${body}`;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Email copied. Paste it into your mail app and give it one last read.");
  } catch (_error) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    showToast("Email copied. Give it one last read before sending.");
  }
}

function chooseFilter(filter) {
  state.filter = filter;
  renderSubscriptions(state.analysis);
}

function bindEvents() {
  $$(".filter-tab").forEach((tab) => {
    tab.addEventListener("click", () => chooseFilter(tab.dataset.filter));
  });

  $("#review-button").addEventListener("click", () => {
    chooseFilter("review");
    $("#stack").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  ["#import-button", "#hero-import", "#closing-import"].forEach((selector) => {
    $(selector).addEventListener("click", openUpload);
  });

  $$(".close-dialog").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });

  $$("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  $("#dropzone").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", (event) => {
    setSelectedFile(event.target.files?.[0]);
  });
  ["dragenter", "dragover"].forEach((type) => {
    $("#dropzone").addEventListener(type, (event) => {
      event.preventDefault();
      $("#dropzone").classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    $("#dropzone").addEventListener(type, (event) => {
      event.preventDefault();
      $("#dropzone").classList.remove("is-dragging");
    });
  });
  $("#dropzone").addEventListener("drop", (event) => {
    setSelectedFile(event.dataTransfer?.files?.[0]);
  });
  $("#upload-form").addEventListener("submit", submitUpload);
  $("#load-demo").addEventListener("click", loadDemo);
  $("#export-link").addEventListener("click", () => {
    showToast("Exporting your subscription ledger as CSV.");
  });

  $("#subscription-grid").addEventListener("click", (event) => {
    const card = event.target.closest("[data-id]");
    if (card) openDetail(card.dataset.id);
  });

  $("#timeline").addEventListener("click", (event) => {
    const item = event.target.closest("[data-id]");
    if (item) openDetail(item.dataset.id);
  });

  $("#ai-recommendations").addEventListener("click", (event) => {
    const item = event.target.closest("[data-id]");
    if (item) openDetail(item.dataset.id);
  });

  $("#detail-dialog").addEventListener("click", (event) => {
    if (event.target.closest("[data-close-detail]")) {
      $("#detail-dialog").close();
      return;
    }
    const usage = event.target.closest("[data-usage]");
    if (usage) {
      updateDecision(state.selectedId, undefined, usage.dataset.usage);
      return;
    }
    const decision = event.target.closest("[data-decision]");
    if (decision) {
      const current = state.analysis?.subscriptions.find((item) => item.id === state.selectedId);
      const next = current?.decision === decision.dataset.decision ? "unreviewed" : decision.dataset.decision;
      updateDecision(state.selectedId, next);
      return;
    }
    if (event.target.closest("[data-email]")) {
      openEmailDialog(state.selectedId);
    }
  });

  $("#email-dialog").addEventListener("click", (event) => {
    if (event.target.closest("#copy-email")) copyEmail();
    if (event.target.closest("#redraft-email")) draftEmail($("#email-reason").value);
    if (event.target.closest("#email-reason-toggle")) {
      const wrap = $("#reason-wrap");
      wrap.hidden = !wrap.hidden;
      if (!wrap.hidden) $("#email-reason").focus();
    }
  });

  const openHow = () => {
    const dialog = $("#how-dialog");
    if (!dialog.open) dialog.showModal();
  };
  $("#how-ai").addEventListener("click", openHow);
  $("#footer-how").addEventListener("click", openHow);
  $("#refresh-ai").addEventListener("click", refreshBrief);
  $("#error-retry").addEventListener("click", init);
}

async function init() {
  bindEvents();
  try {
    const data = await fetchJSON("/api/state");
    if (!data.analysis) {
      showError("There's no analysis yet. Import a CSV to start.");
      return;
    }
    state.analysis = data.analysis;
    render();
  } catch (error) {
    showError(error.message);
  }
}

document.addEventListener("DOMContentLoaded", init);
