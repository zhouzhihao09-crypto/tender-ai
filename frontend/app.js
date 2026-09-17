/* Tender AI — Phase 8 product UI. All data comes from the local API; nothing is invented. */
const state = { overview: null, tenders: [], checklists: [], companyProfile: null, vault: [], vaultById: {}, detailId: null, insight: null, workspace: null, package: null, pollTimer: null, libraryFilter: 'all', search: '', picker: { mode: null, bidDocumentId: null, requestedDocument: '' } };
const authState = { checked: false, user: null, appInitialized: false };
let selectedFile = null;
const $ = (selector) => document.querySelector(selector);

/* ---------- helpers ---------- */
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' })[char]); }
function fmtDate(value) { if (!value) return 'Not stated'; const date = new Date(value); return isNaN(date) ? escapeHtml(value) : new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).format(date); }
function fmtDateTime(value) { if (!value) return ''; const date = new Date(value); return isNaN(date) ? '' : new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date); }
async function api(url, options = {}) {
  const response = await fetch(url, { ...options, credentials: 'include' });
  if (!response.ok) {
    if (response.status === 401 && authState.checked) {
      showAuthGate('Your session has expired. Please sign in again.');
      throw new Error('Your session has expired. Please sign in again.');
    }
    let detail = 'Request failed.';
    try { detail = (await response.json()).detail || detail; } catch (error) { /* keep default */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}
function showAuthGate(message = '') {
  $('#app-shell')?.classList.add('hidden');
  $('#auth-loading')?.classList.add('hidden');
  $('#auth-gate')?.classList.remove('hidden');
  if (message) $('#auth-copy').textContent = message;
}
function showApp(user) {
  authState.user = user;
  $('#account-email').textContent = user.email;
  $('#auth-gate')?.classList.add('hidden');
  $('#auth-loading')?.classList.add('hidden');
  $('#app-shell')?.classList.remove('hidden');
  if (!authState.appInitialized) { authState.appInitialized = true; init(); }
}
async function authRequest(path, payload) {
  const response = await fetch(path, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!response.ok) { let detail = 'Something went wrong. Please try again.'; try { detail = (await response.json()).detail || detail; } catch (error) { /* keep generic */ } throw new Error(detail); }
  return response.json();
}
function bindAuthControls() {
  $('#show-login').addEventListener('click', () => { $('#show-login').classList.add('active'); $('#show-register').classList.remove('active'); $('#login-form').classList.remove('hidden'); $('#register-form').classList.add('hidden'); $('#auth-title').textContent = 'Keep your tender work in one place.'; $('#auth-copy').textContent = 'Sign in to open your workspace.'; });
  $('#show-register').addEventListener('click', () => { $('#show-register').classList.add('active'); $('#show-login').classList.remove('active'); $('#register-form').classList.remove('hidden'); $('#login-form').classList.add('hidden'); $('#auth-title').textContent = 'Create your tender workspace.'; $('#auth-copy').textContent = 'Start with an email and a password. No payment details are needed.'; });
  $('#login-form').addEventListener('submit', async (event) => { event.preventDefault(); const button = $('#login-submit'); const error = $('#login-error'); error.textContent = ''; button.disabled = true; button.setAttribute('aria-busy', 'true'); try { const data = await authRequest('/api/auth/login', { email: $('#login-email').value.trim(), password: $('#login-password').value }); authState.checked = true; showApp(data.user); } catch (err) { error.textContent = err.message; } finally { button.disabled = false; button.removeAttribute('aria-busy'); } });
  $('#register-form').addEventListener('submit', async (event) => { event.preventDefault(); const error = $('#register-error'); error.textContent = ''; if ($('#register-password').value !== $('#register-confirm').value) { error.textContent = 'Passwords do not match.'; return; } const button = $('#register-submit'); button.disabled = true; button.setAttribute('aria-busy', 'true'); try { const data = await authRequest('/api/auth/register', { email: $('#register-email').value.trim(), password: $('#register-password').value }); authState.checked = true; showApp(data.user); } catch (err) { error.textContent = err.message; } finally { button.disabled = false; button.removeAttribute('aria-busy'); } });
  $('#logout-button').addEventListener('click', async () => { try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); } finally { authState.user = null; authState.checked = true; showAuthGate('You have been signed out.'); } });
}
async function bootstrapAuth() {
  bindAuthControls();
  try {
    const response = await fetch('/api/auth/me', { credentials: 'include' });
    if (response.ok) { authState.checked = true; showApp(await response.json()); return; }
    if (response.status === 401) { authState.checked = true; showAuthGate(); return; }
    throw new Error('Authentication could not be checked.');
  } catch (error) {
    $('#auth-copy').textContent = 'The application could not check your session. Refresh and try again.';
    showAuthGate();
  }
}
function sourceLine(item) { return item.source_page ? `<div class="evidence-line"><b>Page ${item.source_page}</b>${item.evidence_type ? ' · ' + escapeHtml(evidenceLabel(item.evidence_type)) : ''}</div>` : '<div class="evidence-line">Source not stated in the tender</div>'; }
function evidenceLabel(value) { return { EXPLICIT: 'Stated in tender', INFERENCE: 'Inference — verify', UNKNOWN: 'Unknown' }[value] || value; }
function itemList(items, renderer, empty) { return items?.length ? items.map(renderer).join('') : `<div class="empty-insight">${empty}</div>`; }
function tenderRow(tenderId) { return state.overview?.tenders?.find((row) => row.id === tenderId) || null; }

/* ---------- status vocabulary (single source of labelling) ---------- */
const LABELS = {
  analysis: { QUEUED: 'Queued', PROCESSING: 'Analysing', COMPLETED: 'Completed', FAILED: 'Failed' },
  workflow: { NEW: 'New', UNDER_REVIEW: 'Under review', BID_DECISION_PENDING: 'Bid decision pending', PREPARING: 'Preparing', READY_FOR_REVIEW: 'Ready for review', READY_TO_SUBMIT: 'Ready to submit', SUBMITTED: 'Submitted', NO_BID: 'No bid' },
  decision: { BID: 'Bid', NO_BID: 'No bid', PENDING: 'Not decided' },
  recommendation: { BID: 'Bid', BID_WITH_REVIEW: 'Bid with review', REVIEW_REQUIRED: 'Review required', NO_BID: 'No bid', INSUFFICIENT_INFORMATION: 'Not enough information' },
  readiness: { NOT_READY: 'Not ready', REVIEW_REQUIRED: 'Review required', ALMOST_READY: 'Almost ready', READY: 'Ready', INSUFFICIENT_INFORMATION: 'Not enough information' },
  requirement: { NOT_STARTED: 'Not started', IN_PROGRESS: 'In progress', READY: 'Ready', MISSING: 'Missing', BLOCKED: 'Blocked', NOT_APPLICABLE: 'Not applicable' },
  document: { MISSING: 'Missing', FOUND: 'Found', REVIEW_REQUIRED: 'Needs review', READY: 'Ready' },
  clarification: { DRAFT: 'Draft', READY_TO_SEND: 'Ready to send', SENT: 'Sent', ANSWERED: 'Answered', CLOSED: 'Closed' },
  package: { NOT_STARTED: 'Not started', ASSEMBLING: 'Assembling', READY_FOR_REVIEW: 'Ready for review', REVIEWED: 'Reviewed', EXPORTED: 'Exported' },
  match: { MATCH: 'Strong match', PARTIAL_MATCH: 'Partial match', NO_MATCH: 'Concern', UNKNOWN: 'Unknown' },
  checklist: { NOT_STARTED: 'Not started', IN_PROGRESS: 'In progress', DONE: 'Done', BLOCKED: 'Blocked' },
  category: { REGISTRATION: 'Registration', LICENCE: 'Licence', CERTIFICATION: 'Certification', INSURANCE: 'Insurance', PROJECT_REFERENCE: 'Project reference', COMPANY_PROFILE: 'Company profile', SAFETY: 'Safety', TECHNICAL: 'Technical', OTHER: 'Other' }
};
const TONE = { good: ['COMPLETED', 'READY', 'MATCH', 'DONE', 'ANSWERED', 'CLOSED', 'SUBMITTED', 'BID', 'READY_TO_SUBMIT'], warn: ['PROCESSING', 'QUEUED', 'IN_PROGRESS', 'ALMOST_READY', 'REVIEW_REQUIRED', 'BID_WITH_REVIEW', 'FOUND', 'READY_TO_SEND', 'SENT', 'READY_FOR_REVIEW', 'REVIEWED', 'ASSEMBLING'], bad: ['FAILED', 'NO_BID', 'MISSING', 'BLOCKED', 'NO_MATCH', 'NOT_READY'] };
function tone(value) { for (const [name, values] of Object.entries(TONE)) if (values.includes(value)) return name; return 'neutral'; }
function badge(value, group) {
  if (value === null || value === undefined || value === '') return '<span class="badge tone-neutral">Not stated</span>';
  const label = (LABELS[group] && LABELS[group][value]) || String(value).replaceAll('_', ' ');
  const icon = { good: '✓', warn: '◷', bad: '✕', neutral: '○' }[tone(value)];
  return `<span class="badge tone-${tone(value)}"><i aria-hidden="true">${icon}</i>${escapeHtml(label)}</span>`;
}
function statusOptions(current, group) { const values = Object.keys(LABELS[group]); return values.map((value) => `<option value="${value}" ${current === value ? 'selected' : ''}>${LABELS[group][value]}</option>`).join(''); }
/* ---------- navigation ---------- */
function showView(view) {
  document.querySelectorAll('.page').forEach((page) => page.classList.add('hidden'));
  const target = $(`#${view}-view`); if (target) target.classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === view));
  document.querySelector('.app-shell')?.classList.remove('nav-open');
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    sidebar.style.removeProperty('transform');
    sidebar.style.removeProperty('transition');
  }
  const navToggle = $('#mobile-nav-toggle');
  if (navToggle) navToggle.setAttribute('aria-expanded', 'false');
  const titles = { dashboard: 'Dashboard', tenders: 'Tenders', tasks: 'Tasks', vault: 'Evidence Vault', profile: 'Company Profile', account: 'Account & usage', detail: 'Tender' };
  $('#page-title').textContent = titles[view] || view;
}
async function loadAccount() {
  const host = $('#account-content');
  try {
    const [plan, usage, catalog, billing] = await Promise.all([api('/api/plan'), api('/api/usage'), api('/api/plans'), api('/api/billing/state')]);
    const metric = (label, value) => `<div class="account-metric"><span>${label}</span><strong>${value}</strong></div>`;
    const limit = (item) => item.limit === null ? 'Unlimited' : `${item.used} / ${item.limit}`;
    const billingNote = billing.cancel_at_period_end && billing.current_period_end ? `<p class="billing-warning">Your subscription will end on ${fmtDate(billing.current_period_end)}.</p>` : billing.subscription_status === 'PAST_DUE' ? '<p class="billing-warning">Your payment needs attention. Manage billing to keep your subscription active.</p>' : '';
    const actions = billing.has_subscription ? `<div class="billing-actions"><button class="secondary-button" id="billing-portal">Manage billing</button><button class="text-button" id="billing-cancel">Cancel at period end</button></div>` : '<p class="payment-note">Payment is not yet available until Stripe test mode is configured.</p>';
    host.innerHTML = `<section class="panel account-summary"><div class="panel-heading"><div><p class="eyebrow">CURRENT PLAN</p><h2>${escapeHtml(plan.display_name)}</h2><p>S$${plan.monthly_price_sgd} / month · ${escapeHtml(billing.subscription_status)}</p>${billing.current_period_end ? `<p>Current period ends ${fmtDate(billing.current_period_end)}</p>` : ''}${billingNote}</div>${actions}</div><div class="account-metrics">${metric('Tenders', limit(usage.tenders))}${metric('AI questions', limit(usage.ai_questions))}${metric('Saved tenders', limit(usage.saved_tenders))}${metric('Team members', limit(usage.team_members))}</div></section><section class="account-plans"><h2>Available plans</h2><div class="plan-grid">${catalog.plans.map((item) => `<article class="plan-card ${item.key === plan.plan ? 'current' : ''}"><h3>${escapeHtml(item.display_name)}</h3><strong>S$${item.monthly_price_sgd}</strong><span>${item.monthly_price_sgd ? 'per month' : 'no monthly charge'}</span><p>${item.tender_limit} tenders · ${item.ai_question_limit} AI questions · ${item.saved_tender_limit === null ? 'Unlimited' : item.saved_tender_limit} saved tenders · ${item.team_member_limit} team member${item.team_member_limit === 1 ? '' : 's'}</p>${item.key === plan.plan ? '<b>Current plan</b>' : item.monthly_price_sgd ? `<button class="primary-button small checkout-plan" data-plan="${item.key}">Start subscription</button>` : ''}</article>`).join('')}</div></section>`;
    document.querySelectorAll('.checkout-plan').forEach((button) => button.addEventListener('click', () => startCheckout(button.dataset.plan)));
    $('#billing-portal')?.addEventListener('click', openBillingPortal);
    $('#billing-cancel')?.addEventListener('click', cancelBilling);
  } catch (error) { host.innerHTML = '<div class="empty-insight">Account details are not available right now.</div>'; }
}
async function startCheckout(plan) {
  try {
    const session = await api('/api/billing/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }) });
    window.location.assign(session.url);
  } catch (error) { window.alert(error.message); }
}
async function openBillingPortal() {
  try { const session = await api('/api/billing/portal', { method: 'POST' }); window.location.assign(session.url); } catch (error) { window.alert(error.message); }
}
async function cancelBilling() {
  if (!window.confirm('Cancel this subscription at the end of the current billing period?')) return;
  try { await api('/api/billing/cancel', { method: 'POST' }); await loadAccount(); } catch (error) { window.alert(error.message); }
}

/* ---------- dashboard ---------- */
function libraryMatchesFilter(row, filter) {
  const workflow = row.workflow_status || 'NEW';
  switch (filter) {
    case 'attention': return row.analysis_status !== 'COMPLETED';
    case 'decision': return row.decision === 'PENDING' && !['NO_BID', 'SUBMITTED'].includes(workflow);
    case 'preparing': return ['UNDER_REVIEW', 'PREPARING'].includes(workflow);
    case 'missing': return row.blockers.some((blocker) => blocker.kind === 'document');
    case 'blocked': return row.blocker_count > 0;
    case 'ready': return ['READY_FOR_REVIEW', 'READY_TO_SUBMIT'].includes(workflow);
    case 'new': return workflow === 'NEW';
    case 'under_review': return workflow === 'UNDER_REVIEW';
    case 'bid_decision_pending': return workflow === 'BID_DECISION_PENDING';
    case 'ready_to_submit': return ['READY_FOR_REVIEW', 'READY_TO_SUBMIT'].includes(workflow);
    case 'submitted': return workflow === 'SUBMITTED';
    case 'no_bid': return workflow === 'NO_BID' || row.decision === 'NO_BID';
    default: return true;
  }
}
function renderDashboard() {
  const overview = state.overview;
  const isEmpty = !overview || overview.totals.tenders === 0;
  $('#dashboard-empty').classList.toggle('hidden', !isEmpty);
  $('#dashboard-content').classList.toggle('hidden', isEmpty);
  $('#nav-tender-count').textContent = overview ? overview.totals.tenders : 0;
  if (isEmpty) return;
  const counts = { attention: 'needs_attention', decision: 'awaiting_decision', preparing: 'preparing', missing: 'missing_documents', blocked: 'blocked', ready: 'ready_for_review' };
  const singular = { attention: 'tender needs your attention', decision: 'awaits your bid decision', preparing: 'bid is being prepared', missing: 'bid is missing required documents', blocked: 'bid has unresolved blockers', ready: 'bid is ready for final review' };
  const plural = { attention: 'tenders need your attention', decision: 'await your bid decision', preparing: 'bids being prepared', missing: 'bids missing required documents', blocked: 'bids blocked by unresolved items', ready: 'bids ready for final review' };
  for (const [key, name] of Object.entries(counts)) {
    const count = overview.actions[name].count;
    $(`#count-${key}`).textContent = count;
    $(`#label-${key}`).textContent = count === 1 ? singular[key] : plural[key];
    $(`[data-filter="${key}"]`).classList.toggle('muted', count === 0);
  }
  $('#deadline-list').innerHTML = overview.deadlines.length ? overview.deadlines.map((row) => {
    const past = row.days_remaining < 0;
    const urgent = row.days_remaining <= 14;
    const chip = past ? '<span class="badge tone-bad"><i aria-hidden="true">✕</i>Closing date passed</span>' : urgent ? `<span class="badge tone-bad"><i aria-hidden="true">◷</i>${row.days_remaining} day${row.days_remaining === 1 ? '' : 's'} left</span>` : `<span class="badge tone-warn"><i aria-hidden="true">◷</i>${row.days_remaining} days left</span>`;
    return `<div class="deadline-row ${urgent ? 'urgent' : ''}"><button class="deadline-main" data-tender-id="${row.tender_id}"><strong>${escapeHtml(row.title)}</strong><span>${escapeHtml(row.reference || 'Reference not stated')} · ${fmtDate(row.closing)}</span></button>${chip}</div>`;
  }).join('') : '<div class="empty-insight">No closing dates have been extracted yet. Open a tender to analyse its key dates.</div>';
  $('#recent-list').innerHTML = overview.recent.length ? overview.recent.map((row) => `<div class="insight-item recent-row"><button class="deadline-main" data-tender-id="${row.tender_id}"><strong>${escapeHtml(row.title)}</strong><span>${fmtDateTime(row.updated_at)}</span></button>${badge(row.readiness_state, 'readiness')}</div>`).join('') : '<div class="empty-insight">Nothing has been updated yet.</div>';
  document.querySelectorAll('#dashboard-view [data-tender-id]').forEach((button) => button.addEventListener('click', () => openTender(Number(button.dataset.tenderId))));
}
/* ---------- tender library ---------- */
function renderLibrary() {
  const rows = state.overview ? state.overview.tenders : [];
  const visible = rows.filter((row) => libraryMatchesFilter(row, state.libraryFilter)).filter((row) => !state.search || `${row.title} ${row.reference || ''} ${row.organization || ''}`.toLowerCase().includes(state.search));
  $('#library-rows').innerHTML = visible.length ? visible.map((row) => {
    const days = row.closing ? Math.ceil((new Date(row.closing) - new Date()) / 86400000) : null;
    const closing = row.closing ? `${fmtDate(row.closing)}${days !== null && days >= 0 && days <= 14 ? ` <span class="badge tone-bad"><i aria-hidden="true">◷</i>${days}d</span>` : ''}` : 'Not stated';
    const blockers = row.blocker_count > 0 ? `<span class="badge tone-bad"><i aria-hidden="true">✕</i>${row.blocker_count} blocker${row.blocker_count === 1 ? '' : 's'}</span>` : '<span class="badge tone-neutral"><i aria-hidden="true">○</i>None</span>';
    return `<tr class="clickable-row" data-tender-id="${row.id}"><td><strong>${escapeHtml(row.title)}</strong><span>${escapeHtml(row.organization || 'Organisation not stated')}</span></td><td>${escapeHtml(row.reference || 'Not stated')}</td><td>${closing}</td><td>${badge(row.decision, 'decision')}</td><td>${badge(row.recommendation, 'recommendation')}</td><td>${badge(row.readiness_state, 'readiness')}</td><td>${blockers}</td><td>→</td></tr>`;
  }).join('') : `<tr><td colspan="8" class="empty-row"><div class="empty-icon">◫</div><strong>No tenders in this view</strong><span>Try another filter, or upload a new tender PDF.</span></td></tr>`;
  document.querySelectorAll('#library-rows .clickable-row').forEach((row) => row.addEventListener('click', () => openTender(Number(row.dataset.tenderId))));
}
function setLibraryFilter(filter, note) {
  state.libraryFilter = filter;
  document.querySelectorAll('#library-filters .chip').forEach((chip) => chip.classList.toggle('active', chip.dataset.filter === filter));
  const noteBox = $('#library-filter-note');
  const chipFilters = ['all', 'new', 'under_review', 'bid_decision_pending', 'preparing', 'ready_to_submit', 'submitted', 'no_bid'];
  if (note && !chipFilters.includes(filter)) { $('#library-filter-note-text').textContent = note; noteBox.classList.remove('hidden'); } else noteBox.classList.add('hidden');
  renderLibrary();
}

/* ---------- data loading ---------- */
async function loadOverview() {
  try {
    state.overview = await api('/api/overview');
    state.tenders = state.overview.tenders;
  } catch (error) { console.error(error); return; }
  renderDashboard();
  renderLibrary();
}
async function loadTasks() {
  const groups = await Promise.all(state.tenders.map(async (row) => ({ row, items: await api(`/api/tenders/${row.id}/checklist`) })));
  const items = groups.flatMap((group) => group.items.map((item) => ({ ...item, tenderId: group.row.id, tenderTitle: group.row.title })));
  state.checklists = items;
  const done = items.filter((item) => item.status === 'DONE').length;
  $('#tasks-summary').textContent = items.length ? `${done} / ${items.length} done` : '';
  $('#tasks-list').innerHTML = items.length ? items.map((item) => `<div class="task-row"><div><strong>${escapeHtml(item.title)}</strong><span><button class="link-button" data-tender-id="${item.tenderId}">${escapeHtml(item.tenderTitle)}</button> · ${item.source_page ? `Page ${item.source_page}` : 'Source not stated'}</span></div><select class="checklist-status" data-item-id="${item.id}" aria-label="Task status">${statusOptions(item.status, 'checklist')}</select></div>`).join('') : '<div class="empty-insight">No tasks yet. Tasks appear here once tenders are analysed.</div>';
  document.querySelectorAll('#tasks-list .link-button').forEach((button) => button.addEventListener('click', () => openTender(Number(button.dataset.tenderId))));
  bindChecklistControls();
}
function bindChecklistControls() {
  document.querySelectorAll('.checklist-status').forEach((select) => select.addEventListener('change', async (event) => {
    const item = await api(`/api/checklist/${event.target.dataset.itemId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: event.target.value }) });
    const local = state.checklists.find((entry) => entry.id === item.id);
    if (local) local.status = item.status;
  }));
}
/* ---------- evidence vault ---------- */
async function loadVault() {
  try {
    state.vault = await api('/api/company-evidence');
    state.vaultById = Object.fromEntries(state.vault.map((document) => [document.id, document]));
  } catch (error) { state.vault = []; }
  renderVault($('#vault-search-input') ? $('#vault-search-input').value : '');
}
function expiryIsStale(value) {
  if (!value) return false;
  const parsed = new Date(value);
  return !isNaN(parsed) && parsed < new Date();
}
function renderVault(query) {
  const list = $('#vault-list');
  if (!list) return;
  list.innerHTML = state.vault.length ? state.vault.map((item) => {
    const stale = expiryIsStale(item.expiry_date);
    const expiry = item.expiry_date ? `Expiry: ${escapeHtml(item.expiry_date)}` : 'No expiry recorded';
    return `<div class="vault-row"><div class="vault-file-icon">▤</div><div class="vault-file-info"><strong>${escapeHtml(item.filename)}</strong><span>${escapeHtml(LABELS.category[item.category] || item.category)} · ${item.page_count} page${item.page_count === 1 ? '' : 's'} · ${expiry}</span>${stale ? '<span class="badge tone-bad"><i aria-hidden="true">◷</i>Expiry date has passed — review this document</span>' : ''}<small>${escapeHtml(item.description || 'No description provided')}</small></div><div class="vault-actions"><button class="secondary-button small vault-preview" data-id="${item.id}">Preview</button><button class="secondary-button small vault-usage" data-id="${item.id}">Used in</button><button class="secondary-button small vault-edit" data-id="${item.id}">Edit</button><button class="secondary-button small danger vault-delete" data-id="${item.id}" aria-label="Delete ${escapeHtml(item.filename)}">×</button></div><div class="vault-detail hidden" data-detail-id="${item.id}"></div></div>`;
  }).join('') : `<div class="empty-insight">${query && query.length >= 3 ? 'No evidence matches this search.' : 'No evidence uploaded yet. Add registration, licence, insurance, project reference or other company PDFs so they can support your bids.'}</div>`;
  document.querySelectorAll('.vault-preview').forEach((button) => button.addEventListener('click', () => openPreview(Number(button.dataset.id))));
  document.querySelectorAll('.vault-usage').forEach((button) => button.addEventListener('click', () => toggleUsage(Number(button.dataset.id))));
  document.querySelectorAll('.vault-edit').forEach((button) => button.addEventListener('click', () => toggleVaultEdit(Number(button.dataset.id))));
  document.querySelectorAll('.vault-delete').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm('Delete this company evidence document? Links to requirements and packages that use it will also be removed.')) return;
    await api(`/api/company-evidence/${button.dataset.id}`, { method: 'DELETE' });
    await loadVault();
  }));
}
async function toggleUsage(documentId) {
  const host = document.querySelector(`[data-detail-id="${documentId}"]`);
  if (!host) return;
  if (!host.classList.contains('hidden')) { host.classList.add('hidden'); return; }
  host.classList.remove('hidden');
  host.innerHTML = '<div class="empty-insight">Checking where this document is used...</div>';
  const usage = await api(`/api/company-evidence/${documentId}/usage`);
  const lines = [];
  if (usage.requirements.length) lines.push(...usage.requirements.map((row) => `<li>Requirement: ${escapeHtml(row.requirement)} — ${escapeHtml(row.tender_title)} (page ${row.page})</li>`));
  if (usage.bids.length) lines.push(...usage.bids.map((row) => `<li>Bid document: ${escapeHtml(row.requested_document || 'Requested document')} — ${escapeHtml(row.tender_title)}</li>`));
  if (usage.packages.length) lines.push(...usage.packages.map((row) => `<li>Submission package: ${escapeHtml(row.tender_title)} (${row.included ? 'included' : 'excluded'})</li>`));
  host.innerHTML = `<div class="usage-panel"><strong>Where this document is used</strong>${lines.length ? `<ul>${lines.join('')}</ul>` : '<p>Not linked to any tender yet. Use the Evidence Picker in the submission workspace to link it.</p>'}</div>`;
}
function toggleVaultEdit(documentId) {
  const host = document.querySelector(`[data-detail-id="${documentId}"]`);
  const document = state.vaultById[documentId];
  if (!host || !document) return;
  if (!host.classList.contains('hidden')) { host.classList.add('hidden'); return; }
  host.classList.remove('hidden');
  const categories = Object.keys(LABELS.category);
  host.innerHTML = `<div class="usage-panel"><strong>Edit document details</strong><div class="vault-edit-form"><select id="edit-category-${documentId}" aria-label="Category">${categories.map((value) => `<option value="${value}" ${document.category === value ? 'selected' : ''}>${LABELS.category[value]}</option>`).join('')}</select><input id="edit-expiry-${documentId}" value="${escapeHtml(document.expiry_date || '')}" placeholder="Expiry date (as written on the document)" aria-label="Expiry date" /><input id="edit-description-${documentId}" value="${escapeHtml(document.description || '')}" placeholder="Description" aria-label="Description" /><button class="primary-button small" id="edit-save-${documentId}">Save details</button></div></div>`;
  $(`#edit-save-${documentId}`).addEventListener('click', async () => {
    await api(`/api/company-evidence/${documentId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ category: $(`#edit-category-${documentId}`).value, expiry_date: $(`#edit-expiry-${documentId}`).value, description: $(`#edit-description-${documentId}`).value }) });
    await loadVault();
  });
}
async function uploadEvidence(event) {
  event.preventDefault();
  const file = $('#vault-file').files[0];
  if (!file || file.type !== 'application/pdf') { $('#vault-error').textContent = 'Choose a PDF evidence document.'; return; }
  const form = new FormData();
  form.append('file', file);
  form.append('category', $('#vault-category').value);
  form.append('description', $('#vault-description').value);
  try {
    await api('/api/company-evidence', { method: 'POST', body: form });
    $('#vault-error').textContent = '';
    event.target.reset();
    event.target.classList.add('hidden');
    await loadVault();
  } catch (error) { $('#vault-error').textContent = error.message; }
}
let previewState = { documentId: null, page: 1, pageCount: 1 };
async function openPreview(documentId, page = 1) {
  const document = state.vaultById[documentId];
  previewState = { documentId, page, pageCount: document ? Math.max(document.page_count, 1) : 1 };
  $('#preview-title').textContent = document ? document.filename : 'Document';
  $('#preview-modal').classList.remove('hidden');
  await renderPreviewPage();
}
async function renderPreviewPage() {
  if (!previewState.documentId) return;
  $('#preview-page-label').textContent = `Page ${previewState.page} of ${previewState.pageCount}`;
  $('#preview-text').textContent = 'Loading extracted text...';
  try {
    const preview = await api(`/api/company-evidence/${previewState.documentId}/preview?page=${previewState.page}`);
    $('#preview-text').textContent = preview.text || 'No extractable text on this page. The PDF may be scanned or image-based.';
  } catch (error) { $('#preview-text').textContent = error.message; }
}
/* ---------- company profile ---------- */
const profileGroups = { Company: [['company_name', 'Company name'], ['description', 'Company description'], ['business_type', 'Business type'], ['industry', 'Industry'], ['years_in_business', 'Years in business', 'number']], Capabilities: [['main_services', 'Main services'], ['areas_of_expertise', 'Areas of expertise'], ['key_capabilities', 'Key capabilities'], ['geographic_coverage', 'Geographic coverage'], ['typical_project_size', 'Typical project size'], ['maximum_project_size', 'Maximum project size'], ['number_of_employees', 'Number of employees', 'number']], 'Licences & registrations': [['relevant_licences', 'Relevant licences'], ['registrations', 'Registrations'], ['bca_registration_info', 'BCA registration information'], ['certifications', 'Certifications']], Experience: [['past_project_experience', 'Past project experience']], Insurance: [['insurance_coverage', 'Insurance coverage']], 'Other information': [['other_qualifications', 'Other relevant qualifications']] };
async function loadCompanyProfile() {
  try { state.companyProfile = await api('/api/company-profile'); } catch (error) { return; }
  renderCompanyProfile();
}
function renderCompanyProfile() {
  const container = $('#profile-panel');
  if (!container || !state.companyProfile) return;
  container.innerHTML = `<div class="profile-explainer"><strong>How this profile is used</strong><p>Your answers are compared against each tender's requirements. Fields you leave empty stay <b>unknown</b> — they are never treated as a failed requirement and never become a NO_BID on their own.</p></div><form class="company-profile-form" id="company-profile-form">${Object.entries(profileGroups).map(([group, fields]) => `<fieldset><legend>${group}</legend><div class="profile-fields">${fields.map(([key, label, type]) => `<label>${label}<input name="${key}" type="${type || 'text'}" value="${escapeHtml(state.companyProfile[key] ?? '')}" placeholder="Not provided" /></label>`).join('')}</div></fieldset>`).join('')}<div class="profile-footer"><span id="profile-save-status">Profile version ${state.companyProfile.version}</span><button class="primary-button" type="submit">Save company profile <span>→</span></button></div></form>`;
  $('#company-profile-form').addEventListener('submit', saveCompanyProfile);
}
async function saveCompanyProfile(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  ['years_in_business', 'number_of_employees'].forEach((field) => { if (payload[field] === '') payload[field] = null; else if (payload[field] !== undefined) payload[field] = Number(payload[field]); });
  const status = $('#profile-save-status');
  try {
    state.companyProfile = await api('/api/company-profile', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    status.textContent = `Saved · Profile version ${state.companyProfile.version}`;
  } catch (error) { status.textContent = 'Could not save profile'; }
}
/* ---------- tender detail ---------- */
async function openTender(id) {
  state.detailId = id;
  showView('detail');
  $('#insight-content').classList.add('hidden');
  $('#analysis-banner').classList.remove('hidden');
  $('#retry-analysis').classList.add('hidden');
  $('#detail-title').innerHTML = 'Loading tender<span>.</span>';
  try {
    state.insight = await api(`/api/tenders/${id}/insight`);
    renderInsight(state.insight);
    if (state.insight.tender.analysis_status === 'COMPLETED') {
      // Each panel handles its own errors; one failing panel must not mask an otherwise successful load.
      for (const loader of [loadBidWorkspace, loadBidPackage, loadCompanyFit]) {
        try { await loader(id); } catch (error) { console.error('Tender panel failed to load:', error); }
      }
    }
    if (['QUEUED', 'PROCESSING'].includes(state.insight.tender.analysis_status)) {
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(() => openTender(id), 1200);
    }
  } catch (error) {
    $('#analysis-banner-title').textContent = 'Could not load tender';
    $('#analysis-banner-copy').textContent = error.message;
  }
}
function renderInsight(data) {
  const analysis = data.analysis || {};
  const summary = analysis.summary || {};
  $('#detail-title').innerHTML = `${escapeHtml(data.tender.title)}<span>.</span>`;
  $('#detail-meta').textContent = `${escapeHtml(summary.organisation || data.tender.organization || 'Organisation not stated')} · ${escapeHtml(summary.reference || data.tender.reference || 'Reference not stated')}`;
  $('#detail-status').textContent = data.tender.analysis_status;
  $('#detail-status').className = `analysis-pill ${data.tender.analysis_status === 'COMPLETED' ? 'complete' : data.tender.analysis_status === 'FAILED' ? 'failed' : ''}`;
  $('#analysis-banner').classList.toggle('hidden', data.tender.analysis_status === 'COMPLETED');
  $('#insight-content').classList.toggle('hidden', data.tender.analysis_status !== 'COMPLETED');
  if (data.tender.analysis_status === 'FAILED') {
    $('#analysis-banner-title').textContent = 'Analysis failed';
    $('#analysis-banner-copy').textContent = data.tender.analysis_error || 'The document could not be analysed. If it is a scan or image-only PDF, text extraction is not possible.';
    $('#retry-analysis').classList.remove('hidden');
  } else if (data.tender.analysis_status === 'PROCESSING') {
    $('#analysis-banner-title').textContent = 'Analysing tender…';
    $('#analysis-banner-copy').textContent = 'Extracting requirements, dates, risks and clarifications from the document.';
    $('#retry-analysis').classList.add('hidden');
  } else if (data.tender.analysis_status === 'QUEUED') {
    $('#analysis-banner-title').textContent = 'Analysis queued';
    $('#analysis-banner-copy').textContent = 'The tender is waiting to be analysed. This happens automatically.';
    $('#retry-analysis').classList.add('hidden');
  } else {
    $('#analysis-banner-title').textContent = 'Analysis queued';
    $('#analysis-banner-copy').textContent = 'The tender is waiting to be analysed.';
    $('#retry-analysis').classList.add('hidden');
  }
  $('#summary-fields').innerHTML = [['Title', summary.title], ['Reference', summary.reference], ['Organisation', summary.organisation], ['Scope', summary.scope], ['Location', summary.location], ['Closing', data.tender.deadline ? fmtDate(data.tender.deadline) : null]].map(([label, value]) => `<div class="summary-field"><span>${label}</span><strong>${escapeHtml(value || 'Not stated in the tender')}</strong></div>`).join('');
  renderAssessment(analysis);
  renderFacts(data);
  renderSources(data);
}
function renderAssessment(analysis) {
  const bid = analysis.bid_assessment || {};
  $('#assessment-content').innerHTML = `<div class="assessment-body">${badge(bid.recommendation || 'REVIEW_REQUIRED', 'recommendation')}<div class="confidence-label">Confidence: ${escapeHtml((bid.confidence || 'LOW').toLowerCase())}</div><p>${escapeHtml(bid.rationale || 'The tender needs review.')}</p><ul class="concern-list">${(bid.reasons || []).map((reason) => `<li>• ${escapeHtml(reason)}</li>`).join('')}${(bid.verify || []).map((reason) => `<li>⚠ Verify: ${escapeHtml(reason)}</li>`).join('')}${(bid.concerns || []).map((concern) => `<li>⚠ ${escapeHtml(concern)}</li>`).join('')}</ul><p class="ai-note">AI analysis based on the tender document only. It is decision support — your bid decision is recorded separately in the submission workspace.</p></div>`;
}
function renderFacts(data) {
  const analysis = data.analysis || {};
  $('#requirements-list').innerHTML = itemList(data.requirements, (item) => `<div class="insight-item"><div class="insight-title">${escapeHtml(item.requirement)} ${item.mandatory ? '<span class="status">Mandatory</span>' : ''}</div><div class="insight-copy">${escapeHtml(item.category)}</div>${sourceLine(item)}</div>`, 'No submission requirements were identified in the tender document.');
  $('#documents-list').innerHTML = itemList(data.documents, (item) => `<div class="insight-item"><div class="insight-title">${escapeHtml(item.name)} ${item.required ? '<span class="status">Required</span>' : ''}</div><div class="insight-copy">${item.required ? 'Required for submission' : 'Mentioned in the tender'}</div>${sourceLine(item)}</div>`, 'No requested documents were identified.');
  $('#document-count').textContent = `${data.documents?.length || 0} items`;
  $('#dates-list').innerHTML = itemList(analysis.dates, (item) => `<div class="insight-item"><div class="insight-title">${escapeHtml(item.event)}</div><div class="insight-copy">${escapeHtml(item.date)}${item.time ? ` at ${escapeHtml(item.time)}` : ''}</div>${sourceLine(item)}</div>`, 'No dates were found in the tender document. Dates are never invented.');
  $('#risks-list').innerHTML = itemList(data.risks, (item) => `<div class="insight-item"><div class="insight-title"><span class="severity ${item.severity.toLowerCase()}">${escapeHtml(item.severity)}</span>${escapeHtml(item.title)}</div><div class="insight-copy">${escapeHtml(item.explanation)}<br><b>Next:</b> ${escapeHtml(item.recommended_action)}</div>${sourceLine(item)}</div>`, 'No risks were identified in the tender document.');
  $('#clarifications-list').innerHTML = itemList(analysis.clarifications, (item) => `<div class="insight-item"><div class="insight-title">${escapeHtml(item.question)}</div><div class="insight-copy">${escapeHtml(item.why_it_matters)}</div>${sourceLine(item)}</div>`, 'No clarification topics were identified. You can still ask the buyer questions before the clarification deadline.');
  $('#sources-list').innerHTML = itemList(data.sources, (item) => `<div class="source-evidence-item"><span>Page ${item.page}</span><p>${escapeHtml(item.snippet.slice(0, 360))}</p></div>`, 'No stored source excerpts are available yet.');
}
async function loadCompanyFit(id) {
  const host = $('#company-fit-content');
  const generateBtn = $('#generate-company-fit');
  try {
    const data = await api(`/api/tenders/${id}/company-assessment`, { method: 'POST' });
    state.companyFit = data;
    const groups = { MATCH: [], PARTIAL_MATCH: [], UNKNOWN: [], NO_MATCH: [] };
    data.matches.forEach((match) => (groups[match.match_status] || groups.UNKNOWN).push(match));
    const section = (title, rows, caption) => `<div class="fit-group"><h4>${title} <em>${rows.length}</em></h4>${rows.length ? rows.map((match) => `<div class="match-row"><div><strong>${escapeHtml(matchRequirementTitle(match))}</strong><span>${escapeHtml(match.company_field ? match.company_field.replaceAll('_', ' ') : 'Company information')}</span></div><div class="company-evidence">${escapeHtml(match.company_value || 'Not provided — unknown')}</div><div class="fit-why">${escapeHtml(match.company_explanation)}</div></div>`).join('') : `<div class="empty-insight">${caption}</div>`}</div>`;
    host.innerHTML = `<div class="company-summary">${badge(data.recommendation, 'recommendation')}<span>Readiness score: ${data.readiness_score === null ? 'Not enough information' : data.readiness_score + ' / 100'}</span><em>Profile v${data.profile_version} · decision support only — this is not your bid decision</em></div><p class="company-explanation">${escapeHtml(data.explanation)}</p><div class="fit-groups">${section('Strong matches', groups.MATCH, 'No strong matches yet.')}${section('Partial matches', groups.PARTIAL_MATCH, 'No partial matches.')}${section('Unknowns — information missing', groups.UNKNOWN, 'No unknown fields.')}${section('Potential concerns', groups.NO_MATCH, 'No explicit concerns.')}</div>${data.actions.length ? `<div class="fit-actions"><h4>Verify before you decide</h4>${data.actions.map((action) => `<div class="company-action"><div><strong>${escapeHtml(action.title)}</strong><span>${escapeHtml(action.reason)}${action.source_page ? ` · Tender page ${action.source_page}` : ''}</span></div><select class="company-action-status" data-action-id="${action.id}" aria-label="Status for ${escapeHtml(action.title)}">${statusOptions(action.status, 'checklist')}</select></div>`).join('')}</div>` : ''}`;
    document.querySelectorAll('.company-action-status').forEach((select) => select.addEventListener('change', () => api(`/api/company-actions/${select.dataset.actionId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: select.value }) })));
    if (generateBtn) generateBtn.classList.add('hidden');
  } catch (error) {
    const message = error.message || '';
    if (message.includes('not available yet') || message.includes('must be complete')) {
      host.innerHTML = `<div class="empty-insight">Company fit has not been generated yet. Generate it once the tender analysis is complete to compare your company profile against the tender requirements.</div>`;
    } else {
      host.innerHTML = `<div class="empty-insight">Company fit is not available right now: ${escapeHtml(message)}.</div>`;
    }
    if (generateBtn) generateBtn.classList.remove('hidden');
  }
}
function matchRequirementTitle(match) {
  const requirement = (state.insight.requirements || []).find((item) => item.id === match.requirement_id);
  return requirement ? requirement.requirement : 'Tender requirement';
}
async function askQuestion(event) {
  event.preventDefault();
  const question = $('#ask-question').value.trim();
  if (!question || !state.detailId) return;
  const answer = $('#ask-answer');
  const submit = $('#ask-form button[type="submit"]');
  answer.classList.remove('hidden');
  answer.textContent = 'Searching the tender document...';
  answer.classList.add('is-loading');
  if (submit) { submit.disabled = true; submit.setAttribute('aria-busy', 'true'); }
  try {
    const data = await api(`/api/tenders/${state.detailId}/ask?question=${encodeURIComponent(question)}`, { method: 'POST' });
    answer.innerHTML = `<div>${escapeHtml(data.answer)}</div>${(data.sources || []).map((source) => `<span class="source-chip">Page ${source.page}</span>`).join('')}`;
    answer.classList.remove('is-loading');
  } catch (error) {
    answer.classList.remove('is-loading');
    answer.classList.add('is-error');
    answer.textContent = 'The tender could not be searched right now. Please try again.';
  } finally {
    if (submit) { submit.disabled = false; submit.removeAttribute('aria-busy'); }
  }
}
/* ---------- upload modal ---------- */
function openModal() { $('#upload-modal').classList.remove('hidden'); }
function closeModal() { $('#upload-modal').classList.add('hidden'); resetUpload(); }
function resetUpload() {
  selectedFile = null;
  $('#upload-form').reset();
  $('#dropzone').classList.remove('hidden');
  $('#selected-file').classList.add('hidden');
  $('#upload-progress').classList.add('hidden');
  $('#form-error').classList.add('hidden');
  $('#upload-submit').disabled = true;
}
function chooseFile(file) {
  if (!file) return;
  if (file.type !== 'application/pdf') { showError('Please select a PDF file.'); return; }
  selectedFile = file;
  $('#file-name').textContent = file.name;
  $('#dropzone').classList.add('hidden');
  $('#selected-file').classList.remove('hidden');
  $('#upload-submit').disabled = false;
}
function showError(message) { const error = $('#form-error'); error.textContent = message; error.classList.remove('hidden'); }
async function uploadTender(event) {
  event.preventDefault();
  const file = selectedFile;
  if (!file) return;
  const progress = $('#upload-progress');
  const fill = $('#progress-fill');
  const label = $('#progress-step');
  const percent = $('#progress-percent');
  $('#upload-submit').disabled = true;
  progress.classList.remove('hidden');
  fill.style.width = '0%';
  percent.textContent = '0%';
  label.textContent = 'Uploading document';
  const formData = new FormData();
  formData.append('file', file);
  try {
    const data = await api('/api/tenders', { method: 'POST', body: formData });
    fill.style.width = '100%';
    percent.textContent = '100%';
    label.textContent = 'Analysis queued';
    await new Promise((resolve) => setTimeout(resolve, 300));
    closeModal();
    await loadOverview();
    openTender(data.id);
  } catch (error) {
    showError(error.message);
    $('#upload-submit').disabled = false;
    progress.classList.add('hidden');
  }
}
/* ---------- init ---------- */
function init() {
  const navToggle = $('#mobile-nav-toggle');
  if (navToggle) navToggle.addEventListener('click', () => {
    const open = document.querySelector('.app-shell').classList.toggle('nav-open');
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
      if (open) {
        sidebar.style.setProperty('transition', 'none', 'important');
        sidebar.style.setProperty('transform', 'none', 'important');
      } else {
        sidebar.style.removeProperty('transform');
        sidebar.style.removeProperty('transition');
      }
    }
    navToggle.setAttribute('aria-expanded', String(open));
    navToggle.setAttribute('aria-label', open ? 'Close workspace navigation' : 'Open workspace navigation');
  });
  document.querySelectorAll('[data-view]').forEach((item) => item.addEventListener('click', async () => {
    showView(item.dataset.view);
    if (item.dataset.view === 'tasks') await loadTasks();
    if (item.dataset.view === 'vault') await loadVault();
    if (item.dataset.view === 'profile') await loadCompanyProfile();
    if (item.dataset.view === 'account') await loadAccount();
  }));
  ['open-upload', 'library-upload', 'topbar-upload', 'dashboard-empty-upload'].forEach((id) => { const button = $(`#${id}`); if (button) button.addEventListener('click', openModal); });
  $('#close-modal').addEventListener('click', closeModal);
  $('#remove-file').addEventListener('click', resetUpload);
  $('#file-input').addEventListener('change', (event) => chooseFile(event.target.files[0]));
  $('#dropzone').addEventListener('dragover', (event) => event.preventDefault());
  $('#dropzone').addEventListener('drop', (event) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0]); });
  $('#upload-form').addEventListener('submit', uploadTender);
  $('#ask-form').addEventListener('submit', askQuestion);
  $('#back-to-library').addEventListener('click', async () => { showView('tenders'); await loadOverview(); });
  $('#retry-analysis').addEventListener('click', async () => { await api(`/api/tenders/${state.detailId}/retry`, { method: 'POST' }); openTender(state.detailId); });
  $('#reanalyze-tender').addEventListener('click', async () => {
    if (!window.confirm('Re-analyze this tender? Existing extracted intelligence will be replaced. Your submission statuses are kept.')) return;
    await api(`/api/tenders/${state.detailId}/reanalyze`, { method: 'POST' });
    openTender(state.detailId);
  });
  $('#generate-company-fit').addEventListener('click', async () => {
    const button = $('#generate-company-fit');
    button.disabled = true;
    try {
      await api(`/api/tenders/${state.detailId}/company-assessment`, { method: 'POST' });
      await loadCompanyFit(state.detailId);
    } catch (error) {
      window.alert(error.message);
    } finally {
      button.disabled = false;
    }
  });
  document.querySelectorAll('#library-filters .chip').forEach((chip) => chip.addEventListener('click', () => setLibraryFilter(chip.dataset.filter)));
  const clearFilter = $('#clear-library-filter');
  if (clearFilter) clearFilter.addEventListener('click', () => setLibraryFilter('all'));
  document.querySelectorAll('.action-card').forEach((card) => card.addEventListener('click', () => {
    showView('tenders');
    const filter = card.dataset.filter;
    setLibraryFilter(filter);
    $('#library-filter-note-text').textContent = { attention: 'Showing tenders whose analysis is not complete.', decision: 'Showing tenders that await your bid decision.', preparing: 'Showing bids currently being prepared.', missing: 'Showing bids with missing required documents.', blocked: 'Showing bids blocked by unresolved items.', ready: 'Showing bids ready for final review.' }[filter] || '';
    $('#library-filter-note').classList.remove('hidden');
  }));
  $('#search-input').addEventListener('input', (event) => { state.search = event.target.value.toLowerCase(); renderLibrary(); });
  $('#vault-toolbar-upload').addEventListener('click', () => $('#vault-upload-form').classList.toggle('hidden'));
  $('#vault-upload-form').addEventListener('submit', uploadEvidence);
  $('#vault-search-input').addEventListener('input', async (event) => {
    const query = event.target.value.trim();
    if (query.length >= 3) {
      try {
        const results = await api(`/api/company-evidence/search?q=${encodeURIComponent(query)}`);
        state.vault = results.map((row) => ({ id: row.document_id, filename: row.filename, category: row.category, page_count: 1, expiry_date: (state.vaultById[row.document_id] || {}).expiry_date || null, description: row.snippet }));
      } catch (error) { state.vault = []; }
      renderVault(query);
    } else { await loadVault(); }
  });
  $('#close-picker').addEventListener('click', () => $('#picker-modal').classList.add('hidden'));
  $('#close-preview').addEventListener('click', () => $('#preview-modal').classList.add('hidden'));
  $('#preview-prev').addEventListener('click', async () => { if (previewState.page > 1) { previewState.page -= 1; await renderPreviewPage(); } });
  $('#preview-next').addEventListener('click', async () => { if (previewState.page < previewState.pageCount) { previewState.page += 1; await renderPreviewPage(); } });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { $('#picker-modal').classList.add('hidden'); $('#preview-modal').classList.add('hidden'); $('#upload-modal').classList.add('hidden'); } });
  loadOverview();
  loadVault();
  loadCompanyProfile();
}
document.addEventListener('DOMContentLoaded', bootstrapAuth);










