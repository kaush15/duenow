// DueNow â€” Application Controller
// Plain HTML/CSS/JS, no framework, no build step.

const API_BASE = window.location.protocol === "file:" ? "http://localhost:8000" : "";

const state = {
  currentTab: "dashboard",
  simulatedDate: "",
  offsetDays: 0,
  demoStatus: null,
  dashboardStats: null,
  invoices: [],
  invoiceFilter: "all",
  invoiceSearch: "",
  queue: [],
  clients: [],
  logs: [],
  smartDraft: null,
  chartInstance: null
};

// Toast notifications â€” bottom-left, aria-live polite
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.setAttribute("role", "status");
  toast.innerHTML = `
    <span class="toast-dot"></span>
    <span class="toast-msg">${message}</span>
    <button class="toast-close" onclick="this.closest('.toast').remove()" aria-label="Dismiss">&times;</button>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    if (toast.parentElement) {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(8px)";
      toast.style.transition = "opacity 0.2s ease, transform 0.2s ease";
      setTimeout(() => toast.remove(), 220);
    }
  }, 4500);
}

// Format Currency using Intl
function formatMoney(amount, currency = "USD") {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(Number(amount) || 0);
  } catch (e) {
    const symbols = { USD: "$", EUR: "â‚¬", GBP: "Â£", CAD: "CA$", AUD: "A$", JPY: "Â¥" };
    const sym = symbols[currency] || (currency + " ");
    return `${sym}${Number(amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
}

// Format Date using Intl
function formatDate(dateStr) {
  if (!dateStr) return "â€”";
  try {
    const d = new Date(dateStr);
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(d);
  } catch (e) {
    return dateStr;
  }
}

// Toggle Sidebar
function toggleSidebar() {
  const sidebar = document.getElementById("app-sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (!sidebar) return;

  if (window.innerWidth >= 1024) {
    // Desktop: collapse to the left
    sidebar.classList.toggle("desktop-collapsed");
  } else {
    // Mobile: slide in/out
    const isOpen = sidebar.classList.contains("mobile-open");
    if (isOpen) {
      sidebar.classList.remove("mobile-open");
      if (backdrop) backdrop.classList.remove("visible");
    } else {
      sidebar.classList.add("mobile-open");
      if (backdrop) backdrop.classList.add("visible");
    }
  }
}

// Close mobile sidebar
function closeMobileSidebar() {
  const sidebar = document.getElementById("app-sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (sidebar) sidebar.classList.remove("mobile-open");
  if (backdrop) backdrop.classList.remove("visible");
}

// Keyboard shortcut (Ctrl+B or Cmd+B) to toggle sidebar
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
    e.preventDefault();
    toggleSidebar();
  }
});

// Tab Switching â€” no animation, just show/hide via CSS class
function switchTab(tabName) {
  state.currentTab = tabName;

  // Hide all panes
  document.querySelectorAll(".tab-pane").forEach(el => {
    el.classList.add("hidden");
    el.removeAttribute("aria-current");
  });

  // Deactivate all nav buttons
  document.querySelectorAll(".sidebar-nav-item").forEach(el => {
    el.classList.remove("active");
    el.removeAttribute("aria-current");
  });

  // Show selected pane
  const activePane = document.getElementById(`pane-${tabName}`);
  if (activePane) {
    activePane.classList.remove("hidden");
  }

  // Activate sidebar item
  const activeNav = document.getElementById(`nav-${tabName}`);
  if (activeNav) {
    activeNav.classList.add("active");
    activeNav.setAttribute("aria-current", "page");
  }

  // Update breadcrumb
  const breadcrumbEl = document.getElementById("header-breadcrumb");
  if (breadcrumbEl) {
    const titles = {
      dashboard:      "Dashboard",
      "smart-create": "New invoice",
      queue:          "Reminders",
      invoices:       "Invoices",
      clients:        "Clients",
      logs:           "Audit log"
    };
    breadcrumbEl.textContent = titles[tabName] || "Dashboard";
  }

  // Auto-close mobile sidebar
  if (window.innerWidth < 1024) {
    closeMobileSidebar();
  }

  // Load data for the tab
  if (tabName === "dashboard") loadDashboard();
  if (tabName === "queue") loadQueue();
  if (tabName === "invoices") loadInvoices();
  if (tabName === "clients") loadClients();
  if (tabName === "logs") loadLogs();
}

// -------------------------------------------------------------
// API Calls & Data Loading
// -------------------------------------------------------------

async function fetchStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/demo/status`);
    if (!res.ok) throw new Error("Status failed");
    const data = await res.json();
    state.demoStatus = data;
    state.simulatedDate = data.simulated_date;
    state.offsetDays = data.offset_days;

    // Update DOM indicators
    const dateEl = document.getElementById("header-simulated-date");
    if (dateEl) {
      dateEl.textContent = formatDate(data.simulated_date);
    }
    const offsetEl = document.getElementById("header-offset-badge");
    if (offsetEl) {
      offsetEl.textContent = data.offset_days > 0 ? `+${data.offset_days}d test` : "Today";
      offsetEl.className = data.offset_days > 0 ? "topbar-env-badge offset" : "topbar-env-badge";
    }

    const ppBadge = document.getElementById("header-paypal-badge");
    if (ppBadge) {
      if (data.paypal_configured) {
        ppBadge.innerHTML = `<span class="conn-dot"></span>PayPal sandbox`;
      } else {
        ppBadge.innerHTML = `<span class="conn-dot" style="background:var(--status-sent-dot)"></span>PayPal offline`;
      }
    }

    const llmBadge = document.getElementById("header-llm-badge");
    if (llmBadge) {
      llmBadge.textContent = "AI ready";
    }

    const autoApproveToggle = document.getElementById("auto-approve-toggle");
    if (autoApproveToggle) {
      autoApproveToggle.checked = Boolean(data.auto_approve_friendly);
    }
  } catch (err) {
    console.warn("Failed to fetch demo status:", err);
  }
}

async function fastForwardTime(days = 7) {
  try {
    const res = await fetch(`${API_BASE}/api/demo/fast-forward?days=${days}`, { method: "POST" });
    const data = await res.json();
    showToast(`Fast-forwarded +${days} days to ${formatDate(data.simulated_date)}`, "success");
    await fetchStatus();
    await reloadAll();
  } catch (e) {
    showToast("Fast forward failed: " + e.message, "error");
  }
}

async function resetTime() {
  try {
    const res = await fetch(`${API_BASE}/api/demo/reset-time`, { method: "POST" });
    const data = await res.json();
    showToast(`Date reset to today (${formatDate(data.simulated_date)})`, "info");
    await fetchStatus();
    await reloadAll();
  } catch (e) {
    showToast("Reset time failed: " + e.message, "error");
  }
}

async function seedData() {
  if (!confirm("Reset database with sample clients and invoices?")) return;
  try {
    const res = await fetch(`${API_BASE}/api/demo/seed?reset=true`, { method: "POST" });
    const data = await res.json();
    showToast(data.message || "Sample invoices loaded!", "success");
    await fetchStatus();
    await reloadAll();
  } catch (e) {
    showToast("Seed failed: " + e.message, "error");
  }
}

async function runAutopilotNow() {
  const btn = document.getElementById("btn-run-autopilot");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-3.5 w-3.5 text-white inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg> Checking...`;
  }

  try {
    const res = await fetch(`${API_BASE}/api/autopilot/run`, { method: "POST" });
    const data = await res.json();
    const msg = `Checked ${data.evaluated_invoices} invoices: ${data.new_proposals} follow-up reminders drafted, ${data.auto_approved} sent automatically.`;
    showToast(msg, "success");
    await reloadAll();
  } catch (e) {
    showToast("Reminder check failed: " + e.message, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Run autopilot`;
    }
  }
}

async function toggleAutoApprove() {
  const toggle = document.getElementById("auto-approve-toggle");
  const newVal = toggle ? toggle.checked : false;
  try {
    await fetch(`${API_BASE}/api/demo/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_approve_friendly: newVal ? "true" : "false" })
    });
    showToast(`Automatic friendly reminders are now ${newVal ? "ENABLED" : "DISABLED"}`, "info");
    await fetchStatus();
  } catch (e) {
    showToast("Setting update failed", "error");
  }
}

// -------------------------------------------------------------
// DASHBOARD
// -------------------------------------------------------------

async function loadDashboard() {
  try {
    const res = await fetch(`${API_BASE}/api/invoices/dashboard/stats`);
    if (!res.ok) throw new Error("Dashboard API failed");
    const data = await res.json();
    state.dashboardStats = data;

    // Summary strip
    document.getElementById("stat-outstanding").textContent = formatMoney(data.total_outstanding);
    document.getElementById("stat-overdue").textContent = formatMoney(data.total_overdue);
    document.getElementById("stat-collected").textContent = formatMoney(data.total_collected);
    document.getElementById("stat-avg-days").textContent = `${data.avg_days_to_pay} days`;

    // Forecast / insight
    if (data.cashflow_insight) {
      const ins = data.cashflow_insight;
      document.getElementById("insight-summary-text").textContent = ins.summary;
      document.getElementById("insight-projected-amt").textContent = formatMoney(ins.projected_30d);
      const confBadge = document.getElementById("insight-confidence-badge");
      if (confBadge) {
        const lvl = (ins.confidence_level || "High").toLowerCase();
        confBadge.textContent = `${ins.confidence_level || "High"} confidence`;
        confBadge.className = `text-xs ${lvl === "high" ? "text-muted" : lvl === "medium" ? "" : ""}`;
      }
    }

    // "Needs attention" list â€” overdue invoices
    renderAttentionList(data);

    // Chart
    renderChart(data.chart_data);
  } catch (e) {
    console.error("Dashboard error:", e);
  }
}

function renderAttentionList(data) {
  const list = document.getElementById("dashboard-attention-list");
  if (!list) return;

  // Try to use overdue invoices if present, else use insight data
  const overdueInvoices = data.overdue_invoices || [];
  list.innerHTML = "";

  if (overdueInvoices.length === 0) {
    list.innerHTML = `<li style="padding:16px 0; color:var(--color-ink-3); font-size:13px;">No overdue invoices. Nice work.</li>`;
    return;
  }

  overdueInvoices.slice(0, 6).forEach(inv => {
    const li = document.createElement("li");
    li.className = "attention-item";
    li.innerHTML = `
      <div class="attention-client">
        <div class="attention-client-name">${inv.client_name || "Client"}</div>
        <div class="attention-late">${inv.days_overdue || 0} days overdue</div>
      </div>
      <div class="attention-amount">${formatMoney(inv.amount, inv.currency)}</div>
      <button class="attention-action" onclick="openCustomRemindModal(${inv.id})" title="Send reminder to ${inv.client_name}">
        Remind
      </button>
    `;
    list.appendChild(li);
  });
}

function renderChart(chartData) {
  const ctx = document.getElementById("cashflow-chart");
  if (!ctx || !window.Chart) return;

  if (state.chartInstance) {
    state.chartInstance.destroy();
  }

  if (!chartData || chartData.length === 0) return;

  const labels = chartData.map(d => d.period);
  const collectedValues = chartData.map(d => d.collected);
  const projectedValues = chartData.map(d => d.projected);

  const fontUI = "'Instrument Sans', -apple-system, sans-serif";
  const fontMono = "'IBM Plex Mono', monospace";

  state.chartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Collected",
          data: collectedValues,
          backgroundColor: "#0F5C4D",
          hoverBackgroundColor: "#0A4A3D",
          borderRadius: 3,
          barPercentage: 0.6,
          categoryPercentage: 0.65
        },
        {
          label: "Expected",
          data: projectedValues,
          backgroundColor: "#D0CEC6",
          hoverBackgroundColor: "#B8B6AE",
          borderRadius: 3,
          barPercentage: 0.6,
          categoryPercentage: 0.65
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#1B1B18",
          titleFont: { family: fontUI, size: 12, weight: "500" },
          bodyFont: { family: fontMono, size: 12 },
          padding: 10,
          cornerRadius: 6,
          callbacks: {
            label: context => ` ${context.dataset.label}: ${formatMoney(context.raw || 0)}`
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            font: { family: fontUI, size: 11 },
            color: "#9B9A93"
          }
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(230, 227, 219, 0.7)" },
          border: { display: false, dash: [3, 3] },
          ticks: {
            font: { family: fontMono, size: 11 },
            color: "#9B9A93",
            callback: value => formatMoney(value)
          }
        }
      }
    }
  });
}

// -------------------------------------------------------------
// SMART CREATE (Natural Language Invoice Extraction)
// -------------------------------------------------------------

function usePromptPreset(text) {
  const input = document.getElementById("smart-prompt-input");
  if (input) {
    input.value = text;
    input.focus();
  }
}

async function extractSmartDraft() {
  const promptInput = document.getElementById("smart-prompt-input");
  const currencySelect = document.getElementById("smart-currency-select");
  const extractBtn = document.getElementById("btn-extract-draft");

  const promptText = promptInput.value.trim();
  if (!promptText) {
    showToast("Please describe the work done or amount to invoice", "error");
    return;
  }

  extractBtn.disabled = true;
  extractBtn.innerHTML = `<svg class="animate-spin" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Draftingâ€¦`;

  document.getElementById("draft-preview-section").classList.add("hidden");
  document.getElementById("clarification-box").classList.add("hidden");

  try {
    const res = await fetch(`${API_BASE}/api/smart-create/parse`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: promptText,
        currency: currencySelect.value
      })
    });

    if (!res.ok) throw new Error("Invoice generation failed");
    const data = await res.json();

    if (data.is_clarification_needed) {
      document.getElementById("clarification-box").classList.remove("hidden");
      document.getElementById("clarification-question-text").textContent = data.clarification_question;
      document.getElementById("clarification-reply-input").value = "";
      document.getElementById("clarification-reply-input").focus();
      showToast("One question before drafting", "info");
    } else if (data.invoice_draft) {
      state.smartDraft = data.invoice_draft;
      renderDraftPreview(data.invoice_draft);
      const preview = document.getElementById("draft-preview-section");
      const placeholder = document.getElementById("draft-placeholder");
      if (preview) preview.classList.remove("hidden");
      if (placeholder) placeholder.classList.add("hidden");
      showToast("Invoice draft ready â€” review before sending", "success");
    }
  } catch (e) {
    showToast("Could not generate invoice draft: " + e.message, "error");
  } finally {
    extractBtn.disabled = false;
    extractBtn.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="20 6 9 17 4 12"/></svg>
      Draft invoice
    `;
  }
}

function submitClarificationReply() {
  const replyInput = document.getElementById("clarification-reply-input");
  const promptInput = document.getElementById("smart-prompt-input");
  const reply = replyInput.value.trim();
  if (!reply) return;

  promptInput.value = `${promptInput.value}. Note: ${reply}`;
  document.getElementById("clarification-box").classList.add("hidden");
  extractSmartDraft();
}

function renderDraftPreview(draft) {
  document.getElementById("draft-client-name").value = draft.client_name || "";
  document.getElementById("draft-client-email").value = draft.client_email || "";
  document.getElementById("draft-due-date").value = draft.due_date ? draft.due_date.substring(0, 10) : "";
  document.getElementById("draft-currency").value = draft.currency || "USD";
  document.getElementById("draft-note").value = draft.note || "Thank you for your business.";
  document.getElementById("draft-terms").value = draft.terms || "Payment due upon receipt.";

  // Update invoice number display if present
  const numDisplay = document.getElementById("draft-inv-number-display");
  if (numDisplay) numDisplay.textContent = draft.invoice_number ? `#${draft.invoice_number}` : "Draft";

  renderLineItems(draft.items || []);
}

function renderLineItems(items) {
  const container = document.getElementById("draft-line-items-body");
  container.innerHTML = "";

  items.forEach((item, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <input type="text" class="form-control item-name" value="${item.name || ''}" placeholder="Service description" style="height:28px; font-size:12px;">
      </td>
      <td style="text-align:center;">
        <input type="number" min="1" step="0.5" class="form-control item-qty" value="${item.quantity || 1}" oninput="recalcDraftTotal()" style="height:28px; font-size:12px; text-align:center; width:70px;">
      </td>
      <td style="text-align:right;">
        <input type="number" min="0" step="0.01" class="form-control item-price" value="${item.unit_price || 0}" oninput="recalcDraftTotal()" style="height:28px; font-size:12px; text-align:right; width:90px;">
      </td>
      <td class="text-right mono item-subtotal" style="font-size:12px; padding-right:8px;">
        ${formatMoney((item.quantity || 1) * (item.unit_price || 0))}
      </td>
      <td style="text-align:center;">
        <button onclick="removeLineItem(${index})" class="btn btn-ghost btn-sm" style="color:var(--color-ink-3); padding:0 4px;" title="Remove line" aria-label="Remove line item">&times;</button>
      </td>
    `;
    container.appendChild(row);
  });

  recalcDraftTotal();
}

function addLineItem() {
  const container = document.getElementById("draft-line-items-body");
  const row = document.createElement("tr");
  row.innerHTML = `
    <td>
      <input type="text" class="form-control item-name" placeholder="Service description" value="" style="height:28px; font-size:12px;">
    </td>
    <td style="text-align:center;">
      <input type="number" min="1" step="1" class="form-control item-qty" value="1" oninput="recalcDraftTotal()" style="height:28px; font-size:12px; text-align:center; width:70px;">
    </td>
    <td style="text-align:right;">
      <input type="number" min="0" step="0.01" class="form-control item-price" value="100.00" oninput="recalcDraftTotal()" style="height:28px; font-size:12px; text-align:right; width:90px;">
    </td>
    <td class="text-right mono item-subtotal" style="font-size:12px; padding-right:8px;">$100.00</td>
    <td style="text-align:center;">
      <button onclick="this.closest('tr').remove(); recalcDraftTotal();" class="btn btn-ghost btn-sm" style="color:var(--color-ink-3); padding:0 4px;" title="Remove" aria-label="Remove line item">&times;</button>
    </td>
  `;
  container.appendChild(row);
  recalcDraftTotal();
}

function recalcDraftTotal() {
  const container = document.getElementById("draft-line-items-body");
  let total = 0;
  const currency = document.getElementById("draft-currency")?.value || "USD";

  container.querySelectorAll("tr").forEach(row => {
    const qty = parseFloat(row.querySelector(".item-qty")?.value || 0);
    const price = parseFloat(row.querySelector(".item-price")?.value || 0);
    const sub = qty * price;
    const subEl = row.querySelector(".item-subtotal");
    if (subEl) subEl.textContent = formatMoney(sub, currency);
    total += sub;
  });

  const totalEl = document.getElementById("draft-calculated-total");
  if (totalEl) totalEl.textContent = formatMoney(total, currency);

  const totalRowEl = document.getElementById("draft-total-row");
  if (totalRowEl) totalRowEl.textContent = formatMoney(total, currency);
}

function removeLineItem(index) {
  const container = document.getElementById("draft-line-items-body");
  const rows = container.querySelectorAll("tr");
  if (rows[index]) {
    rows[index].remove();
    recalcDraftTotal();
  }
}

async function submitInvoice(sendNow = true) {
  const clientName = document.getElementById("draft-client-name").value.trim();
  const clientEmail = document.getElementById("draft-client-email").value.trim();
  const dueDate = document.getElementById("draft-due-date").value;
  const currency = document.getElementById("draft-currency").value;
  const note = document.getElementById("draft-note").value;
  const terms = document.getElementById("draft-terms").value;

  if (!clientName || !clientEmail) {
    showToast("Please enter client name and client email", "error");
    return;
  }

  const items = [];
  const rows = document.querySelectorAll("#draft-line-items-body tr");
  rows.forEach(r => {
    const name = r.querySelector(".item-name").value.trim();
    const qty = parseFloat(r.querySelector(".item-qty").value || 1);
    const price = parseFloat(r.querySelector(".item-price").value || 0);
    if (name) {
      items.push({ name, quantity: qty, unit_price: price, description: name });
    }
  });

  if (items.length === 0) {
    showToast("Please add at least one service or item to invoice", "error");
    return;
  }

  const btn = sendNow ? document.getElementById("btn-create-and-send") : document.getElementById("btn-save-draft");
  btn.disabled = true;
  const origText = btn.innerHTML;
  btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg> Connecting to PayPal...`;

  try {
    const payload = {
      client_name: clientName,
      client_email: clientEmail,
      currency: currency,
      items: items,
      due_date: dueDate || new Date().toISOString().substring(0, 10),
      note: note,
      terms: terms,
      send_now: sendNow
    };

    const res = await fetch(`${API_BASE}/api/invoices`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Invoice creation failed");
    }

    const created = await res.json();
    showToast(`Invoice #${created.invoice_number} successfully ${sendNow ? "sent to client" : "saved as draft"}!`, "success");

    openInvoiceSuccessModal(created);

    document.getElementById("smart-prompt-input").value = "";
    document.getElementById("draft-preview-section").classList.add("hidden");
    await reloadAll();
  } catch (e) {
    showToast("Could not send invoice: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = origText;
  }
}

function openInvoiceSuccessModal(inv) {
  const modal = document.getElementById("invoice-success-modal");
  document.getElementById("modal-inv-number").textContent = inv.invoice_number;
  document.getElementById("modal-inv-client").textContent = inv.client_name;
  document.getElementById("modal-inv-amount").textContent = formatMoney(inv.amount, inv.currency);
  document.getElementById("modal-inv-status").textContent = inv.status;
  document.getElementById("modal-inv-paypal-id").textContent = inv.paypal_invoice_id || "LOCAL-SANDBOX";

  const viewBtn = document.getElementById("modal-payer-view-btn");
  if (viewBtn) {
    const viewUrl = inv.view_url || (inv.paypal_invoice_id ? `https://www.sandbox.paypal.com/invoice/p/#${inv.paypal_invoice_id}` : "#");
    viewBtn.href = viewUrl;
    viewBtn.target = "_blank";
  }

  modal.classList.remove("hidden");
}

function closeInvoiceSuccessModal() {
  document.getElementById("invoice-success-modal").classList.add("hidden");
}

// -------------------------------------------------------------
// AUTOPILOT FOLLOW-UP QUEUE
// -------------------------------------------------------------

async function loadQueue() {
  try {
    const res = await fetch(`${API_BASE}/api/autopilot/queue`);
    if (!res.ok) throw new Error("Failed to load queue");
    const queue = await res.json();
    state.queue = queue;

    const badge = document.getElementById("queue-count-badge");
    if (badge) {
      badge.textContent = queue.length;
      badge.className = queue.length > 0 ? "nav-count has-items" : "nav-count";
    }

    renderQueueMailView(queue);
  } catch (e) {
    console.error("Queue load error:", e);
  }
}

function renderQueueMailView(queue) {
  const listEl = document.getElementById("queue-list-items");
  const detailEl = document.getElementById("queue-detail-pane");
  const emptyState = document.getElementById("queue-empty-state");

  if (!listEl || !detailEl) return;

  if (!queue || queue.length === 0) {
    listEl.innerHTML = `<div style="padding:24px 16px;font-size:12px;color:var(--color-ink-3);text-align:center;">No reminders in queue</div>`;
    detailEl.innerHTML = `
      <div class="empty-state" id="queue-empty-state">
        <div class="empty-state-title">No reminders in queue</div>
        <p class="empty-state-desc">All invoices are up to date, or reminders have already been sent. Use "+7d" above to simulate overdue invoices.</p>
        <button onclick="fastForwardTime(7)" class="btn btn-secondary">Simulate +7 days</button>
      </div>
    `;
    return;
  }
  listEl.innerHTML = "";

  queue.forEach((item, idx) => {
    const inv = item.invoice || {};
    const initials = (inv.client_name || "CL").split(" ").map(w => w[0]).join("").substring(0, 2).toUpperCase();
    const toneLabel = item.tone === "friendly" ? "Friendly" : item.tone === "firm" ? "Firm" : "Final notice";
    const daysOverdue = inv.days_overdue || 0;

    const listItem = document.createElement("div");
    listItem.className = "queue-list-item" + (idx === 0 ? " active" : "");
    listItem.dataset.queueId = item.id;
    listItem.setAttribute("role", "button");
    listItem.setAttribute("tabindex", "0");
    listItem.innerHTML = `
      <div class="queue-list-avatar">${initials}</div>
      <div class="queue-list-meta">
        <div class="queue-list-name">${inv.client_name || "Client"}</div>
        <div class="queue-list-sub">${inv.invoice_number || ""} &middot; ${formatMoney(inv.amount, inv.currency)}</div>
        <div class="queue-list-tags">
          <span class="queue-tone-pill tone-${item.tone}">${toneLabel}</span>
          ${daysOverdue > 0 ? `<span class="queue-overdue-label">${daysOverdue}d overdue</span>` : ""}
        </div>
      </div>
    `;
    listItem.addEventListener("click", () => {
      listEl.querySelectorAll(".queue-list-item").forEach(el => el.classList.remove("active"));
      listItem.classList.add("active");
      renderQueueDetailPane(item, queue);
    });
    listItem.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); listItem.click(); }
    });
    listEl.appendChild(listItem);
  });

  // Auto-select first item
  if (queue.length > 0) renderQueueDetailPane(queue[0], queue);
}

// Alias for legacy callers
function renderQueueCards(queue) { renderQueueMailView(queue); }

function renderQueueDetailPane(item, queue) {
  const detailEl = document.getElementById("queue-detail-pane");
  if (!detailEl) return;

  const inv = item.invoice || {};
  const initials = (inv.client_name || "CL").split(" ").map(w => w[0]).join("").substring(0, 2).toUpperCase();
  const scoreColor = (item.client_score >= 80) ? "var(--status-paid-dot)" : (item.client_score >= 55) ? "var(--status-sent-dot)" : "var(--status-overdue-dot)";
  const scoreWidth = Math.min(100, item.client_score || 70);
  const friendlyActive = item.tone === "friendly" ? " active" : "";
  const firmActive = item.tone === "firm" ? " active" : "";
  const finalActive = item.tone === "final_notice" ? " active" : "";
  const escapedSubject = (item.subject || "").replace(/"/g, "&quot;");

  detailEl.innerHTML = `
    <div class="queue-detail-header">
      <div class="queue-detail-avatar">${initials}</div>
      <div class="queue-detail-header-meta">
        <div class="queue-detail-client">${inv.client_name || "Client"}</div>
        <div class="queue-detail-inv">${inv.invoice_number || ""} &middot; ${formatMoney(inv.amount, inv.currency)} &middot; due ${formatDate(inv.due_date)}</div>
      </div>
    </div>

    <div class="queue-email-section">
      <div class="queue-email-row">
        <span class="queue-email-label">To</span>
        <span class="queue-email-value">${inv.client_email || ""}</span>
      </div>
      <div class="queue-email-row">
        <span class="queue-email-label">Subject</span>
        <input type="text" id="queue-subj-${item.id}" class="form-control queue-email-input" value="${escapedSubject}">
      </div>
    </div>

    <div class="queue-tone-row">
      <span class="queue-tone-label">Tone</span>
      <div class="tone-seg" role="group" aria-label="Reminder tone">
        <button class="tone-seg-btn${friendlyActive}" onclick="switchQueueTone(${item.id}, 'friendly', state.queue)">Friendly</button>
        <button class="tone-seg-btn${firmActive}" onclick="switchQueueTone(${item.id}, 'firm', state.queue)">Firm</button>
        <button class="tone-seg-btn${finalActive}" onclick="switchQueueTone(${item.id}, 'final_notice', state.queue)">Final notice</button>
      </div>
    </div>

    <textarea id="queue-msg-${item.id}" class="form-control queue-msg-area" rows="8">${item.message || ""}</textarea>

    <div class="queue-reasoning-box">
      <div class="queue-reasoning-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
        Autopilot reasoning
      </div>
      <div class="queue-reasoning-text">${item.reasoning || ""}</div>
      ${item.client_score != null ? `
      <div class="queue-score-row">
        <span class="queue-score-label">Client reliability</span>
        <div class="score-bar-track" style="width:120px">
          <div class="score-bar-fill" style="width:${scoreWidth}%;background:${scoreColor}"></div>
        </div>
        <span class="score-num">${item.client_score}/100</span>
      </div>` : ""}
    </div>

    <div class="queue-actions">
      <button class="btn btn-ghost btn-sm" onclick="handleQueueAction(${item.id}, 'skip')">Skip</button>
      <button class="btn btn-secondary btn-sm" onclick="handleQueueAction(${item.id}, 'edit')">Edit &amp; send</button>
      <button class="btn btn-primary btn-sm" onclick="handleQueueAction(${item.id}, 'approve')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        Approve &amp; send
      </button>
    </div>
  `;
}

async function switchQueueTone(queueId, tone, queue) {
  const item = (queue || state.queue).find(q => q.id === queueId);
  if (!item) return;
  try {
    const res = await fetch(`${API_BASE}/api/autopilot/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice_id: item.invoice_id, tone: tone, language: "English" })
    });
    if (!res.ok) throw new Error("Draft failed");
    const draft = await res.json();
    item.tone = tone;
    item.subject = draft.subject;
    item.message = draft.message;
    item.reasoning = draft.reasoning;
    renderQueueDetailPane(item, state.queue);
    // Update tone pill in list
    const listEl = document.getElementById("queue-list-items");
    if (listEl) {
      const activeItem = listEl.querySelector(".queue-list-item.active");
      if (activeItem) {
        const pill = activeItem.querySelector(".queue-tone-pill");
        if (pill) {
          const label = tone === "friendly" ? "Friendly" : tone === "firm" ? "Firm" : "Final notice";
          pill.className = "queue-tone-pill tone-" + tone;
          pill.textContent = label;
        }
      }
    }
  } catch (e) {
    showToast("Could not redraft: " + e.message, "error");
  }
}

async function handleQueueAction(queueId, action) {
  const subjEl = document.getElementById(`queue-subj-${queueId}`);
  const msgEl = document.getElementById(`queue-msg-${queueId}`);

  const subject = subjEl ? subjEl.value : null;
  const message = msgEl ? msgEl.value : null;

  try {
    const res = await fetch(`${API_BASE}/api/autopilot/action/${queueId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, subject, message })
    });

    if (!res.ok) throw new Error("Action failed");
    const data = await res.json();
    showToast(data.message || `Reminder updated`, "success");
    await loadQueue();
    await loadInvoices();
    await loadDashboard();
  } catch (e) {
    showToast("Action error: " + e.message, "error");
  }
}

// -------------------------------------------------------------
// INVOICES MANAGEMENT
// -------------------------------------------------------------

async function loadInvoices() {
  try {
    const res = await fetch(`${API_BASE}/api/invoices`);
    if (!res.ok) throw new Error("Failed to load invoices");
    const invoices = await res.json();
    state.invoices = invoices;
    renderInvoicesTable();
  } catch (e) {
    console.error("Invoices load error:", e);
  }
}

function filterInvoices(filter) {
  state.invoiceFilter = filter;
  document.querySelectorAll(".inv-filter-btn").forEach(btn => {
    btn.classList.remove("active");
    btn.setAttribute("aria-selected", "false");
  });
  const activeBtn = document.getElementById(`inv-filter-${filter.toLowerCase()}`);
  if (activeBtn) {
    activeBtn.classList.add("active");
    activeBtn.setAttribute("aria-selected", "true");
  }
  renderInvoicesTable();
}

function handleInvoiceSearch(query) {
  state.invoiceSearch = query.toLowerCase();
  renderInvoicesTable();
}

function renderInvoicesTable() {
  const tbody = document.getElementById("invoices-table-body");
  if (!tbody) return;

  let filtered = state.invoices;

  // Status Filter
  if (state.invoiceFilter !== "all") {
    filtered = filtered.filter(i => (i.effective_status || i.status) === state.invoiceFilter.toUpperCase());
  }

  // Search Filter
  if (state.invoiceSearch) {
    filtered = filtered.filter(i => 
      (i.invoice_number && i.invoice_number.toLowerCase().includes(state.invoiceSearch)) ||
      (i.client_name && i.client_name.toLowerCase().includes(state.invoiceSearch)) ||
      (i.client_email && i.client_email.toLowerCase().includes(state.invoiceSearch))
    );
  }

  tbody.innerHTML = "";

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">No invoices found. <button class="btn btn-secondary btn-sm" onclick="switchTab('smart-create')">Draft one from notes.</button></div></td></tr>`;
    return;
  }

  filtered.forEach(inv => {
    const status = inv.effective_status || inv.status;
    let statusClass = "draft";
    if (status === "SENT") statusClass = "sent";
    if (status === "PAID" || status === "MARKED_AS_PAID") statusClass = "paid";
    if (status === "OVERDUE") statusClass = "overdue";
    if (status === "CANCELLED") statusClass = "cancelled";

    let dueTd = formatDate(inv.due_date);
    if (status === "OVERDUE" && inv.days_overdue > 0) {
      dueTd = `<span style="color:var(--status-overdue-color)">${formatDate(inv.due_date)}<br><span style="font-size:11px">${inv.days_overdue}d overdue</span></span>`;
    } else if (inv.days_until_due > 0 && status === "SENT") {
      dueTd = `${formatDate(inv.due_date)}<br><span style="font-size:11px;color:var(--color-ink-3)">in ${inv.days_until_due}d</span>`;
    }

    const payerUrl = inv.view_url || (inv.paypal_invoice_id ? `https://www.sandbox.paypal.com/invoice/p/#${inv.paypal_invoice_id}` : "#");
    const initials = (inv.client_name || "CL").split(" ").map(w => w[0]).join("").substring(0, 2).toUpperCase();
    const scoreColor = inv.client_score >= 80 ? "var(--status-paid-dot)" : inv.client_score >= 55 ? "var(--status-sent-dot)" : "var(--status-overdue-dot)";

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="col-id">${inv.invoice_number || "—"}${inv.is_demo ? ` <span class="badge" style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--color-bg-subtle, #f1f5f9);color:var(--color-ink-3, #64748b);border:1px solid var(--color-border, #e2e8f0);margin-left:4px;">Demo invoice</span>` : ""}</td>
      <td>
        <div class="client-cell">
          <div class="client-avatar">${initials}</div>
          <div>
            <div class="client-name">${inv.client_name || "—"}</div>
            <div class="client-email">${inv.client_email || ""}</div>
          </div>
        </div>
      </td>
      <td class="col-num">${formatMoney(inv.amount, inv.currency)}</td>
      <td>${dueTd}</td>
      <td>
        <span class="status status-${statusClass}">
          <span class="status-dot"></span>${status}
        </span>
      </td>
      <td>
        <div style="display:flex;align-items:center;gap:6px">
          <div class="score-bar-track" style="width:56px">
            <div class="score-bar-fill" style="width:${inv.client_score || 70}%;background:${scoreColor}"></div>
          </div>
          <span class="score-num">${inv.client_score || 70}</span>
        </div>
      </td>
      <td class="row-actions">
        <div style="display:flex;align-items:center;gap:4px;justify-content:flex-end">
          <a href="${payerUrl}" target="_blank" class="btn btn-secondary btn-sm" title="View as client in PayPal sandbox">View</a>
          ${status === "DRAFT" ? `<button class="btn btn-secondary btn-sm" onclick="sendExistingInvoice(${inv.id})">Send</button>` : ""}
          ${(status === "SENT" || status === "OVERDUE") ? `
            <button class="btn btn-secondary btn-sm" onclick="openCustomRemindModal(${inv.id})">Remind</button>
            <button class="btn btn-secondary btn-sm" onclick="recordInvoicePayment(${inv.id})">Mark paid</button>
          ` : ""}
          ${(status !== "CANCELLED" && status !== "PAID" && status !== "MARKED_AS_PAID") ? `
            <button class="btn btn-ghost btn-sm" onclick="cancelExistingInvoice(${inv.id})" title="Cancel invoice" style="color:var(--status-overdue-color)">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          ` : ""}
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function sendExistingInvoice(id) {
  try {
    const res = await fetch(`${API_BASE}/api/invoices/${id}/send`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Send failed");
    }
    showToast("Invoice sent to client!", "success");
    await reloadAll();
  } catch (e) {
    showToast("Error sending invoice: " + e.message, "error");
  }
}

async function recordInvoicePayment(id) {
  try {
    const res = await fetch(`${API_BASE}/api/invoices/${id}/record-payment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: "DEMO_PAYPAL", note: "Marked as paid" })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Payment record failed");
    }
    showToast("Payment recorded! Client reliability score updated.", "success");
    await reloadAll();
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

async function cancelExistingInvoice(id) {
  if (!confirm("Are you sure you want to cancel this invoice?")) return;
  try {
    const res = await fetch(`${API_BASE}/api/invoices/${id}/cancel`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Cancel failed");
    }
    showToast("Invoice cancelled", "info");
    await reloadAll();
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

// -------------------------------------------------------------
// CLIENT PAYMENT CRM
// -------------------------------------------------------------

async function loadClients() {
  try {
    const res = await fetch(`${API_BASE}/api/clients`);
    if (!res.ok) throw new Error("Clients load failed");
    const clients = await res.json();
    state.clients = clients;
    renderClientsCards(clients);
  } catch (e) {
    console.error("Clients error:", e);
  }
}

function renderClientsCards(clients) {
  renderClientsTable(clients);
}

function renderClientsTable(clients) {
  const tbody = document.getElementById("clients-table-body");
  if (!tbody) return;

  tbody.innerHTML = "";

  if (!clients || clients.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">No clients yet. Create an invoice to add a client.</div></td></tr>`;
    return;
  }

  clients.forEach(c => {
    const initials = (c.name || "CL").split(" ").map(w => w[0]).join("").substring(0, 2).toUpperCase();
    const score = c.reliability_score || 0;
    const scoreColor = score >= 80 ? "var(--status-paid-dot)" : score >= 55 ? "var(--status-sent-dot)" : "var(--status-overdue-dot)";
    const speedLabel = c.predicted_pay_days === 0
      ? `<span style="color:var(--status-paid-color)">Prompt (on time)</span>`
      : `<span style="color:var(--status-sent-color)">Usually ~${c.predicted_pay_days}d late</span>`;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div class="client-cell">
          <div class="client-avatar">${initials}</div>
          <div>
            <div class="client-name">${c.name || "—"}</div>
            <div class="client-email">${c.email || ""}</div>
          </div>
        </div>
      </td>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <div class="score-bar-track" style="width:80px">
            <div class="score-bar-fill" style="width:${score}%;background:${scoreColor}"></div>
          </div>
          <span class="score-num">${score}/100</span>
        </div>
        <div style="font-size:11px;color:var(--color-ink-3);margin-top:2px">${c.score_explanation || ""}</div>
      </td>
      <td style="font-size:12px">${speedLabel}</td>
      <td class="col-num">${formatMoney(c.total_billed)}</td>
      <td class="col-num" style="color:var(--status-paid-color)">${formatMoney(c.total_paid)}</td>
      <td class="row-actions">
        <button class="btn btn-secondary btn-sm" onclick="recalculateClientScore(${c.id})">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>
          Update score
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function recalculateClientScore(clientId) {
  try {
    const res = await fetch(`${API_BASE}/api/clients/${clientId}/recalculate-score`, { method: "POST" });
    if (!res.ok) throw new Error("Recalculate failed");
    const data = await res.json();
    showToast(`Updated score: ${data.reliability_score}/100`, "success");
    await loadClients();
    await loadInvoices();
  } catch (e) {
    showToast("Update error: " + e.message, "error");
  }
}

// -------------------------------------------------------------
// SYSTEM AUDIT & ACTIVITY LOGS
// -------------------------------------------------------------

async function loadLogs() {
  try {
    const res = await fetch(`${API_BASE}/api/autopilot/logs?limit=50`);
    if (!res.ok) throw new Error("Logs load failed");
    const logs = await res.json();
    state.logs = logs;
    renderLogsList(logs);
  } catch (e) {
    console.error("Logs error:", e);
  }
}

function renderLogsList(logs) {
  const container = document.getElementById("logs-container");
  if (!container) return;

  container.innerHTML = "";

  if (!logs || logs.length === 0) {
    container.innerHTML = `<div class="empty-state">No activity recorded yet.</div>`;
    return;
  }

  logs.forEach(log => {
    const ts = new Date(log.created_at);
    const timeStr = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
    }).format(ts);

    let actorLabel = "System";
    if (log.actor === "user") actorLabel = "You";
    else if (log.actor === "paypal_webhook") actorLabel = "PayPal";

    const ref = log.target_id ? `<span class="log-ref">#${log.target_id}</span>` : "";

    const row = document.createElement("div");
    row.className = "log-row";
    row.innerHTML = `
      <span class="log-ts">${timeStr}</span>
      <span class="log-actor">${actorLabel}</span>
      <span class="log-body">
        <span class="log-action">${log.action || ""}</span>
        <span class="log-detail">${log.details || ""}</span>
        ${ref}
      </span>
    `;
    container.appendChild(row);
  });
}

// -------------------------------------------------------------
// REMINDER MODAL
// -------------------------------------------------------------

let activeRemindInvoiceId = null;

async function openCustomRemindModal(invoiceId) {
  activeRemindInvoiceId = invoiceId;
  const modal = document.getElementById("custom-remind-modal");
  modal.classList.remove("hidden");

  document.getElementById("modal-remind-subj").value = "Drafting reminder email...";
  document.getElementById("modal-remind-msg").value = "Reviewing invoice details and payment timing...";
  document.getElementById("modal-remind-reason").textContent = "Drafting suggestion…";

  try {
    const tone = document.getElementById("modal-remind-tone").value || "friendly";
    const res = await fetch(`${API_BASE}/api/autopilot/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice_id: invoiceId, tone: tone, language: "English" })
    });
    if (!res.ok) throw new Error("Draft failed");
    const draft = await res.json();
    document.getElementById("modal-remind-subj").value = draft.subject;
    document.getElementById("modal-remind-msg").value = draft.message;
    document.getElementById("modal-remind-reason").textContent = draft.reasoning;
  } catch (e) {
    document.getElementById("modal-remind-subj").value = "Friendly payment reminder";
    document.getElementById("modal-remind-msg").value = "Hi there, just following up on this invoice. Please let me know if you have any questions!";
    document.getElementById("modal-remind-reason").textContent = "Standard courtesy reminder draft.";
  }
}

async function onToneChangeInModal() {
  if (activeRemindInvoiceId) {
    openCustomRemindModal(activeRemindInvoiceId);
  }
}

function closeCustomRemindModal() {
  document.getElementById("custom-remind-modal").classList.add("hidden");
  activeRemindInvoiceId = null;
}

async function sendCustomModalReminder() {
  if (!activeRemindInvoiceId) return;
  const subj = document.getElementById("modal-remind-subj").value;
  const msg = document.getElementById("modal-remind-msg").value;
  const tone = document.getElementById("modal-remind-tone").value;

  try {
    const res = await fetch(`${API_BASE}/api/invoices/${activeRemindInvoiceId}/remind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject: subj, note: msg, tone: tone, language: "English" })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Remind failed");
    }
    showToast("Reminder sent to client via PayPal!", "success");
    closeCustomRemindModal();
    await reloadAll();
  } catch (e) {
    showToast("Error sending reminder: " + e.message, "error");
  }
}

// -------------------------------------------------------------
// RELOAD ALL STATE
// -------------------------------------------------------------

async function reloadAll() {
  await fetchStatus();
  if (state.currentTab === "dashboard") await loadDashboard();
  if (state.currentTab === "queue") await loadQueue();
  if (state.currentTab === "invoices") await loadInvoices();
  if (state.currentTab === "clients") await loadClients();
  if (state.currentTab === "logs") await loadLogs();
}


// -------------------------------------------------------------
// COMMAND PALETTE
// -------------------------------------------------------------

const CMD_ITEMS = [
  { label: "Dashboard",         keys: "g d", action: () => switchTab("dashboard") },
  { label: "New invoice",       keys: "n",   action: () => switchTab("smart-create") },
  { label: "Invoices",          keys: "g i", action: () => switchTab("invoices") },
  { label: "Reminders queue",   keys: "g r", action: () => switchTab("queue") },
  { label: "Clients",           keys: "g c", action: () => switchTab("clients") },
  { label: "Audit log",         keys: "g l", action: () => switchTab("logs") },
  { label: "Run autopilot",     keys: "",    action: () => runAutopilotNow() },
  { label: "Simulate +7 days",  keys: "",    action: () => fastForwardTime(7) },
  { label: "Reset to today",    keys: "",    action: () => resetTime() },
  { label: "Reload sample data",keys: "",    action: () => seedData() },
];

let cmdActiveFocus = -1;

function openCmdPalette() {
  const backdrop = document.getElementById("cmd-palette-backdrop");
  const input = document.getElementById("cmd-input");
  if (!backdrop || !input) return;
  backdrop.classList.remove("hidden");
  backdrop.setAttribute("aria-hidden", "false");
  input.value = "";
  cmdActiveFocus = -1;
  renderCmdResults("");
  setTimeout(() => input.focus(), 20);
}

function closeCmdPalette() {
  const backdrop = document.getElementById("cmd-palette-backdrop");
  if (backdrop) {
    backdrop.classList.add("hidden");
    backdrop.setAttribute("aria-hidden", "true");
  }
  cmdActiveFocus = -1;
}

function renderCmdResults(query) {
  const listEl = document.getElementById("cmd-results");
  if (!listEl) return;

  const q = query.toLowerCase().trim();
  const matches = q ? CMD_ITEMS.filter(it => it.label.toLowerCase().includes(q)) : CMD_ITEMS;

  if (matches.length === 0) {
    listEl.innerHTML = `<div class="cmd-empty">No results for "${query}"</div>`;
    return;
  }

  listEl.innerHTML = "";
  matches.forEach((item, idx) => {
    const li = document.createElement("div");
    li.className = "cmd-item" + (idx === 0 ? " focused" : "");
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", idx === 0 ? "true" : "false");
    li.innerHTML = `
      <span class="cmd-item-label">${item.label}</span>
      ${item.keys ? `<span class="cmd-item-keys">${item.keys}</span>` : ""}
    `;
    li.addEventListener("mouseenter", () => {
      listEl.querySelectorAll(".cmd-item").forEach((el, i) => {
        el.classList.toggle("focused", i === idx);
        el.setAttribute("aria-selected", i === idx ? "true" : "false");
      });
      cmdActiveFocus = idx;
    });
    li.addEventListener("click", () => {
      item.action();
      closeCmdPalette();
    });
    listEl.appendChild(li);
  });
  cmdActiveFocus = 0;
}

// Command palette keyboard handling
document.addEventListener("keydown", (e) => {
  const backdrop = document.getElementById("cmd-palette-backdrop");
  const isOpen = backdrop && !backdrop.classList.contains("hidden");

  if (isOpen) {
    const listEl = document.getElementById("cmd-results");
    const items = listEl ? listEl.querySelectorAll(".cmd-item") : [];
    if (e.key === "ArrowDown") {
      e.preventDefault();
      cmdActiveFocus = Math.min(cmdActiveFocus + 1, items.length - 1);
      items.forEach((el, i) => { el.classList.toggle("focused", i === cmdActiveFocus); el.setAttribute("aria-selected", i === cmdActiveFocus ? "true" : "false"); });
      items[cmdActiveFocus]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      cmdActiveFocus = Math.max(cmdActiveFocus - 1, 0);
      items.forEach((el, i) => { el.classList.toggle("focused", i === cmdActiveFocus); el.setAttribute("aria-selected", i === cmdActiveFocus ? "true" : "false"); });
      items[cmdActiveFocus]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (cmdActiveFocus >= 0 && items[cmdActiveFocus]) {
        items[cmdActiveFocus].click();
      }
    } else if (e.key === "Escape") {
      closeCmdPalette();
    }
    return;
  }

  // Global shortcuts (not in inputs)
  if (e.key === "Escape") {
    closeCustomRemindModal();
    if (typeof closeInvoiceSuccessModal === "function") closeInvoiceSuccessModal();
  }

  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;

  // Ctrl/Cmd+K = command palette
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openCmdPalette();
    return;
  }

  // Ctrl/Cmd+B = toggle sidebar
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
    e.preventDefault();
    toggleSidebar();
    return;
  }
});

// Two-key g→x navigation
let gKeyPending = false;
document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const backdrop = document.getElementById("cmd-palette-backdrop");
  if (backdrop && !backdrop.classList.contains("hidden")) return;

  if (gKeyPending) {
    gKeyPending = false;
    const map = { d: "dashboard", i: "invoices", r: "queue", c: "clients", l: "logs" };
    if (map[e.key]) {
      e.preventDefault();
      switchTab(map[e.key]);
    }
    return;
  }

  if (e.key === "g") {
    gKeyPending = true;
    setTimeout(() => { gKeyPending = false; }, 1500);
    return;
  }

  if (e.key === "n") {
    e.preventDefault();
    switchTab("smart-create");
  }
});

// Command palette input
document.addEventListener("DOMContentLoaded", () => {
  const cmdInput = document.getElementById("cmd-input");
  if (cmdInput) {
    cmdInput.addEventListener("input", (e) => renderCmdResults(e.target.value));
  }
  // Close on backdrop click
  const backdrop = document.getElementById("cmd-palette-backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) closeCmdPalette();
    });
  }
});

// Initial Boot
document.addEventListener("DOMContentLoaded", async () => {
  await fetchStatus();
  await loadDashboard();
  await loadQueue();
});
