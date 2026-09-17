/* Tender AI — Phase 8 bid workspace, blockers, evidence picker and submission package. Loaded after app.js. */

/* ---------- "What is blocking this bid?" panel ---------- */
function renderBlockers(workspace) {
  const host = $('#blockers-host');
  if (!host) return;
  const blockers = workspace.readiness.blockers || [];
  const attention = workspace.readiness.attention || [];
  if (!blockers.length && !attention.length) {
    host.innerHTML = '<section class="blockers-panel clear"><div class="blockers-head"><h3>What is blocking this bid?</h3></div><p class="blockers-clear">✓ Nothing is blocking it. All tracked items are ready. This is not a compliance determination — you remain responsible for the final check.</p></section>';
    return;
  }
  const item = (blocker, kind) => `<li class="blocker-item ${kind}"><div><strong>${escapeHtml(blocker.label)}</strong><span>${escapeHtml(blocker.action)}</span></div><button class="secondary-button small" data-goto="${blocker.section}">Go to section</button></li>`;
  host.innerHTML = `<section class="blockers-panel"><div class="blockers-head"><h3>What is blocking this bid?</h3><p>Derived from the current statuses. Nothing is assumed.</p></div>${blockers.length ? `<ul class="blocker-list">${blockers.map((blocker) => item(blocker, 'blocking')).join('')}</ul>` : '<p class="blockers-clear">✓ No hard blockers.</p>'}${attention.length ? `<h4>Also needs attention before submission</h4><ul class="blocker-list">${attention.map((entry) => item(entry, 'attention')).join('')}</ul>` : ''}</section>`;
  host.querySelectorAll('[data-goto]').forEach((button) => button.addEventListener('click', () => {
    const target = $(`#bid-section-${button.dataset.goto}`);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
}

/* ---------- bid preparation workspace ---------- */
async function loadBidWorkspace(tenderId) {
  let data;
  try {
    const results = await Promise.all([api(`/api/tenders/${tenderId}/bid-workspace`), api(`/api/tenders/${tenderId}/checklist`).catch(() => [])]);
    data = results[0];
    data.checklist = results[1];
  } catch (error) { return; }
  state.workspace = data;
  renderBlockers(data);
  const host = $('#bid-workspace-host');
  const recommendation = tenderRow(tenderId);
  const decision = `<div class="bid-decision" id="bid-section-decision"><h3>Your bid decision</h3><p>The AI recommendation is separate — this decision is yours.</p><div class="bid-controls"><label>Bid workflow<select id="bid-workflow-status" aria-label="Bid workflow status">${statusOptions(data.workflow_status, 'workflow')}</select></label><label>Your decision<select id="bid-decision" aria-label="Bid decision">${statusOptions(data.decision, 'decision')}</select></label><label>Decision note<input id="bid-decision-note" placeholder="Why you decided this (optional)" value="${escapeHtml(data.decision_note || '')}" aria-label="Decision note" /></label><button class="secondary-button" id="save-bid-decision">Save decision</button></div>${recommendation ? `<p class="ai-inline">AI recommendation: <b>${escapeHtml(LABELS.recommendation[recommendation.recommendation] || recommendation.recommendation)}</b> · decision support only</p>` : ''}${(data.notes || []).length ? `<div class="notes-list"><h4>Internal notes</h4>${data.notes.map((note) => `<div class="note-row"><span>${fmtDateTime(note.created_at)}</span><p>${escapeHtml(note.body)}</p></div>`).join('')}</div>` : ''}<div class="note-add"><input id="new-note-body" placeholder="Add an internal note..." aria-label="Internal note" /><button class="secondary-button small" id="save-note">Add note</button></div></div>`;
  const metrics = `<div class="readiness-metrics"><div><b>${escapeHtml(data.readiness.requirements_ready)}</b><span>requirements ready</span></div><div><b>${escapeHtml(data.readiness.documents_ready)}</b><span>documents ready</span></div><div><b>${data.readiness.blocked}</b><span>blocked</span></div><div><b>${data.readiness.unresolved_clarifications}</b><span>clarifications open</span></div></div>`;
  host.innerHTML = `<section class="bid-workspace-panel"><div class="bid-workspace-header"><div><p class="eyebrow">SUBMISSION WORKSPACE</p><h2>Prepare this submission</h2><p>Statuses are your working record. READY means you confirmed it — not that it is legally compliant.</p></div><div class="readiness-state ${data.readiness.state.toLowerCase()}"><b>${escapeHtml(LABELS.readiness[data.readiness.state] || data.readiness.state)}</b><span>${escapeHtml(data.readiness.reason)}</span></div></div>${decision}${metrics}<div class="bid-workspace-grid"><section id="bid-section-requirements"><h3>Submission requirements</h3>${renderWorkspaceRequirements(data)}</section><section id="bid-section-documents"><h3>Required documents</h3>${renderWorkspaceDocuments(data)}</section><section id="bid-section-missing"><h3>Missing items</h3>${renderMissingItems(data)}</section><section id="bid-section-timeline"><h3>Key dates</h3>${renderTimeline(data)}</section><section id="bid-section-clarifications"><h3>Clarifications</h3>${renderClarifications(data)}</section><section id="bid-section-tasks"><h3>Tasks</h3>${renderWorkspaceTasks(data)}</section></div></section>`;
  bindWorkspaceControls(tenderId);
}
function renderWorkspaceRequirements(data) {
  return data.requirements.length ? data.requirements.map((item) => `<div class="bid-workspace-row"><div><strong>${escapeHtml(item.requirement)}</strong><span>${item.mandatory ? 'Mandatory' : 'Not explicitly mandatory'} · ${item.source_page ? `Tender page ${item.source_page}` : 'Source not stated'}</span></div><select class="bid-requirement-status" data-id="${item.id}" aria-label="Status for ${escapeHtml(item.requirement)}">${statusOptions(item.status, 'requirement')}</select></div>`).join('') : '<div class="empty-insight">No submission requirements were extracted from this tender.</div>';
}
function renderWorkspaceDocuments(data) {
  return data.documents.length ? data.documents.map((item) => {
    const linked = item.evidence_document_ids.map((id) => { const document = state.vaultById[id]; return document ? `<span class="linked-evidence">▤ ${escapeHtml(document.filename)}<button class="unlink-evidence" data-document-id="${id}" data-bid-document-id="${item.id}" aria-label="Unlink ${escapeHtml(document.filename)}">×</button></span>` : ''; }).join('');
    return `<div class="bid-workspace-row document-row"><div><strong>${escapeHtml(item.requested_document)}</strong><span>${item.source_page ? `Tender page ${item.source_page}` : 'Source not stated'}</span><div class="linked-list">${linked || '<span class="linked-empty">No company evidence linked yet.</span>'}</div><button class="secondary-button small open-picker" data-id="${item.id}" data-name="${escapeHtml(item.requested_document)}">Find evidence…</button></div><select class="bid-document-status" data-id="${item.id}" aria-label="Status for ${escapeHtml(item.requested_document)}">${statusOptions(item.status, 'document')}</select></div>`;
  }).join('') : '<div class="empty-insight">No requested documents were extracted from this tender.</div>';
}
function renderMissingItems(data) {
  return data.missing_items.length ? data.missing_items.map((item) => `<div class="missing-item"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.why)}</p><p><b>Next:</b> ${escapeHtml(item.action)}</p><span>${item.source_page ? `Tender page ${item.source_page}` : 'Source not stated'}</span></div>`).join('') : '<div class="empty-insight">No missing items are currently identified.</div>';
}
function renderTimeline(data) {
  return data.timeline.length ? data.timeline.map((item) => `<div class="timeline-item ${/submission|closing/i.test(item.event) ? 'deadline' : ''}"><b>${escapeHtml(item.date)}${item.time ? ` · ${escapeHtml(item.time)}` : ''}</b><span>${escapeHtml(item.event)}${item.source_page ? ` · Page ${item.source_page}` : ''}</span></div>`).join('') : '<div class="empty-insight">No dates were stated in the tender document.</div>';
}
function renderClarifications(data) {
  return data.clarifications.length ? data.clarifications.map((item) => {
    const editable = item.status === 'DRAFT';
    return `<div class="clarification-row"><div class="clarification-head"><strong>${escapeHtml(item.question)}</strong>${badge(item.status, 'clarification')}</div><span>${item.source_page ? `From the tender, page ${item.source_page}` : 'Source not stated'}</span>${editable ? `<textarea class="clarification-draft" data-id="${item.id}" aria-label="Clarification question draft">${escapeHtml(item.question)}</textarea><button class="secondary-button small save-draft" data-id="${item.id}">Save draft</button>` : ''}<div class="clarification-flow"><button class="secondary-button small clar-action" data-id="${item.id}" data-status="READY_TO_SEND" ${item.status === 'DRAFT' ? '' : ''}>Mark ready to send</button><button class="secondary-button small clar-action" data-id="${item.id}" data-status="SENT">Mark sent</button></div><div class="clarification-answer"><label>Answer received<textarea class="answer-input" data-id="${item.id}" placeholder="Record the buyer's answer here (nothing is sent externally)" aria-label="Answer">${escapeHtml(item.answer || '')}</textarea></label><button class="secondary-button small save-answer" data-id="${item.id}">Save answer &amp; mark answered</button></div><button class="text-button clar-close" data-id="${item.id}" ${item.status === 'CLOSED' ? 'disabled' : ''}>Close this clarification</button></div>`;
  }).join('') : '<div class="empty-insight">No clarification questions were identified for this tender.</div>';
}
function renderWorkspaceTasks(data) {
  return data.checklist?.length ? data.checklist.map((item) => `<div class="task-row"><div><strong>${escapeHtml(item.title)}</strong><span>${item.source_page ? `Page ${item.source_page}` : 'Source not stated'}</span></div><select class="checklist-status" data-item-id="${item.id}" aria-label="Status for ${escapeHtml(item.title)}">${statusOptions(item.status, 'checklist')}</select></div>`).join('') : '<div class="empty-insight">No tasks yet. Tasks appear once the tender is analysed.</div>';
}
function bindWorkspaceControls(tenderId) {
  document.querySelectorAll('.bid-requirement-status').forEach((select) => select.addEventListener('change', () => api(`/api/bid-requirements/${select.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: select.value }) }).then(() => loadBidWorkspace(tenderId))));
  document.querySelectorAll('.bid-document-status').forEach((select) => select.addEventListener('change', () => api(`/api/bid-documents/${select.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: select.value }) }).then(() => { loadBidWorkspace(tenderId); loadBidPackage(tenderId); })));
  $('#bid-workflow-status').addEventListener('change', (event) => api(`/api/tenders/${tenderId}/bid-workspace/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflow_status: event.target.value }) }).then(() => loadOverview()));
  $('#save-bid-decision').addEventListener('click', () => api(`/api/tenders/${tenderId}/bid-workspace/decision`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision: $('#bid-decision').value, note: $('#bid-decision-note').value }) }).then(() => { loadBidWorkspace(tenderId); loadOverview(); }));
  $('#save-note').addEventListener('click', async () => {
    const body = $('#new-note-body').value.trim();
    if (!body) return;
    await api(`/api/tenders/${tenderId}/bid-notes`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: 'TENDER', body }) });
    loadBidWorkspace(tenderId);
  });
  document.querySelectorAll('.open-picker').forEach((button) => button.addEventListener('click', () => openPicker('document', Number(button.dataset.id), button.dataset.name, tenderId)));
  document.querySelectorAll('.unlink-evidence').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm('Remove this evidence link? The evidence document itself stays in your vault.')) return;
    await api(`/api/bid-documents/${button.dataset.bidDocumentId}/evidence/${button.dataset.documentId}`, { method: 'DELETE' });
    loadBidWorkspace(tenderId);
  }));
  document.querySelectorAll('.save-draft').forEach((button) => button.addEventListener('click', () => {
    const text = document.querySelector(`.clarification-draft[data-id="${button.dataset.id}"]`).value.trim();
    if (!text) return;
    api(`/api/bid-clarifications/${button.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: text, status: 'DRAFT' }) }).then(() => loadBidWorkspace(tenderId));
  }));
  document.querySelectorAll('.clar-action').forEach((button) => button.addEventListener('click', () => api(`/api/bid-clarifications/${button.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: button.dataset.status }) }).then(() => loadBidWorkspace(tenderId))));
  document.querySelectorAll('.save-answer').forEach((button) => button.addEventListener('click', () => {
    const answer = document.querySelector(`.answer-input[data-id="${button.dataset.id}"]`).value.trim();
    if (!answer) { window.alert('Record the answer text first.'); return; }
    api(`/api/bid-clarifications/${button.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer, status: 'ANSWERED' }) }).then(() => loadBidWorkspace(tenderId));
  }));
  document.querySelectorAll('.clar-close').forEach((button) => button.addEventListener('click', () => api(`/api/bid-clarifications/${button.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'CLOSED' }) }).then(() => loadBidWorkspace(tenderId))));
  document.querySelectorAll('.checklist-status').forEach((select) => select.addEventListener('change', async (event) => { await api(`/api/checklist/${event.target.dataset.itemId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: event.target.value }) }); loadBidWorkspace(tenderId); }));
}
/* ---------- evidence picker (explicit user selection only) ---------- */
let pickerState = { mode: null, bidDocumentId: null, requestedDocument: '', tenderId: null, suggestions: [], linked: [] };
async function openPicker(mode, bidDocumentId, requestedDocument, tenderId) {
  pickerState = { mode, bidDocumentId, requestedDocument, tenderId, suggestions: [], linked: [] };
  $('#picker-kicker').textContent = mode === 'package' ? 'SUBMISSION PACKAGE' : 'EVIDENCE PICKER';
  $('#picker-title').textContent = mode === 'package' ? 'Add company evidence to the package' : 'Link company evidence';
  $('#picker-context').innerHTML = mode === 'package'
    ? 'Choose a document from your vault to include in the submission package. Inclusion is explicit — nothing is added automatically.'
    : `For required document: <b>${escapeHtml(requestedDocument)}</b>. Suggestions come from local text matching only and are <b>potentially relevant</b> — they never prove a requirement is satisfied. Review before linking.`;
  $('#picker-modal').classList.remove('hidden');
  await renderPicker();
}
function pickerCard(document, suggested, page) {
  const stale = expiryIsStale(document.expiry_date);
  return `<div class="picker-card ${suggested ? 'suggested' : ''}"><div class="picker-info"><strong>${escapeHtml(document.filename)}</strong><span>${escapeHtml(LABELS.category[document.category] || document.category)} · ${document.page_count} page${document.page_count === 1 ? '' : 's'} · ${document.expiry_date ? `Expiry: ${escapeHtml(document.expiry_date)}` : 'No expiry recorded'}${stale ? ' · expired' : ''}</span>${suggested && page ? `<small class="picker-snippet">Extracted excerpt (page ${page}): “${escapeHtml(String(document.snippet || '').slice(0, 180))}”</small>` : `<small>${escapeHtml(document.description || 'No description provided')}</small>`}<em>${suggested ? 'Suggested evidence — potentially relevant. Review required.' : 'From your vault.'}</em></div><div class="picker-actions"><button class="text-button picker-preview" data-id="${document.id}">Preview</button><button class="primary-button small picker-link" data-id="${document.id}">${pickerState.mode === 'package' ? 'Add to package' : 'Link this evidence'}</button></div></div>`;
}
async function renderPicker() {
  const suggestionsHost = $('#picker-suggestions');
  const vaultHost = $('#picker-vault');
  const divider = $('#picker-vault-divider');
  suggestionsHost.innerHTML = '<div class="empty-insight">Checking your vault for potentially relevant documents...</div>';
  vaultHost.innerHTML = '';
  if (pickerState.mode === 'document') {
    const data = await api(`/api/tenders/${state.detailId}/bid-documents/${pickerState.bidDocumentId}/evidence-suggestions`);
    pickerState.suggestions = data.suggestions;
    const workspace = await api(`/api/tenders/${state.detailId}/bid-workspace`);
    const document = workspace.documents.find((row) => row.id === pickerState.bidDocumentId);
    pickerState.linked = document ? document.evidence_document_ids : [];
    suggestionsHost.innerHTML = pickerState.suggestions.length ? pickerState.suggestions.map((row) => pickerCard(row, true, row.page)).join('') : '<div class="empty-insight">No obviously matching documents were found. Browse your vault below — linking is always your choice.</div>';
    divider.classList.remove('hidden');
    const suggestedIds = new Set(pickerState.suggestions.map((row) => row.document_id));
    const rest = state.vault.filter((document) => !suggestedIds.has(document.id) && !pickerState.linked.includes(document.id));
    vaultHost.innerHTML = rest.length ? rest.map((document) => pickerCard(document, false)).join('') : '<div class="empty-insight">Every vault document is already suggested or linked.</div>';
  } else {
    divider.classList.add('hidden');
    suggestionsHost.innerHTML = '';
    vaultHost.innerHTML = state.vault.length ? state.vault.map((document) => pickerCard(document, false)).join('') : '<div class="empty-insight">Your vault is empty. Upload company PDFs in the Evidence Vault first.</div>';
  }
  renderPickerLinked();
  bindPickerControls();
}
function renderPickerLinked() {
  const wrap = $('#picker-linked-wrap');
  const host = $('#picker-linked');
  if (pickerState.mode !== 'document') { wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  host.innerHTML = pickerState.linked.length ? pickerState.linked.map((id) => {
    const document = state.vaultById[id];
    if (!document) return '';
    return `<div class="picker-card linked"><div class="picker-info"><strong>${escapeHtml(document.filename)}</strong><span>${escapeHtml(LABELS.category[document.category] || document.category)}</span><em>Linked to this required document — the link is your explicit selection.</em></div><div class="picker-actions"><button class="secondary-button small unlink-evidence" data-document-id="${id}" data-bid-document-id="${pickerState.bidDocumentId}">Unlink</button></div></div>`;
  }).join('') : '<div class="empty-insight">Nothing is linked to this required document yet.</div>';
}
function bindPickerControls() {
  document.querySelectorAll('#picker-modal .picker-link').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (pickerState.mode === 'document') {
        await api(`/api/bid-documents/${pickerState.bidDocumentId}/evidence`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evidence_document_id: Number(button.dataset.id) }) });
        await Promise.all([renderPicker(), loadBidWorkspace(pickerState.tenderId)]);
      } else {
        await api(`/api/tenders/${pickerState.tenderId}/bid-package/evidence`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evidence_document_id: Number(button.dataset.id) }) });
        await Promise.all([renderPicker(), loadBidPackage(pickerState.tenderId)]);
      }
    } catch (error) { window.alert(error.message); button.disabled = false; }
  }));
  document.querySelectorAll('#picker-modal .picker-preview').forEach((button) => button.addEventListener('click', () => openPreview(Number(button.dataset.id))));
  document.querySelectorAll('#picker-modal .unlink-evidence').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm('Remove this evidence link? The evidence document itself stays in your vault.')) return;
    await api(`/api/bid-documents/${button.dataset.bidDocumentId}/evidence/${button.dataset.documentId}`, { method: 'DELETE' });
    await Promise.all([renderPicker(), loadBidWorkspace(pickerState.tenderId)]);
  }));
}
/* ---------- submission package ---------- */
async function loadBidPackage(tenderId) {
  let data;
  try { data = await api(`/api/tenders/${tenderId}/bid-package`); } catch (error) {
    const host = $('#package-host');
    if (host) host.innerHTML = `<section class="package-panel"><div class="package-head"><div><p class="eyebrow">SUBMISSION PACKAGE</p><h2>Submission package</h2></div></div><div class="empty-insight">The submission package could not be loaded: ${escapeHtml(error.message)}</div></section>`;
    return;
  }
  state.package = data;
  const host = $('#package-host');
  const canReview = ['READY', 'ALMOST_READY'].includes(data.readiness.state) && !['REVIEWED', 'EXPORTED'].includes(data.status);
  const readyToSubmit = ['REVIEWED', 'EXPORTED'].includes(data.status) && data.workspace_readiness.state === 'READY' && data.workspace_id && state.workspace && state.workspace.id === data.workspace_id && state.workspace.workflow_status !== 'READY_TO_SUBMIT';
  const blockers = (data.blockers || []).map((blocker) => `<li class="blocker-item blocking"><div><strong>${escapeHtml(blocker.label)}</strong><span>${escapeHtml(blocker.action)}</span></div></li>`).join('');
  const items = data.items.length ? data.items.map((item) => `<div class="package-row"><span class="package-order">${item.display_order}</span><div><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(LABELS.category[item.category] || item.category)} · ${item.source === 'COMPANY_EVIDENCE' ? 'Company evidence' : 'Requested by tender'}${item.related_requirement ? ` · for: ${escapeHtml(item.related_requirement)}` : ''}${item.source_page ? ` · Tender page ${item.source_page}` : ''}</span>${item.evidence_document_id ? `<button class="text-button package-preview" data-id="${item.evidence_document_id}">Preview extracted text</button>` : ''}</div><label class="package-included-label"><input type="checkbox" class="package-included" data-id="${item.id}" ${item.included ? 'checked' : ''} /> Included</label><select class="package-status" data-id="${item.id}" aria-label="Status for ${escapeHtml(item.name)}">${statusOptions(item.status, 'document')}</select><input class="package-notes" data-id="${item.id}" value="${escapeHtml(item.notes || '')}" placeholder="Notes (optional)" aria-label="Notes for ${escapeHtml(item.name)}" /></div>`).join('') : '<div class="empty-insight">The package is empty. Add company evidence below, or update the required documents in the submission workspace.</div>';
  const history = data.exports.length ? data.exports.map((record) => `<div class="export-row"><span>${fmtDateTime(record.created_at)}</span><div><strong>${escapeHtml(record.filename)}</strong><span>${record.included_count} document(s) · ${record.incomplete ? 'exported as incomplete' : `readiness: ${escapeHtml(LABELS.readiness[record.readiness_state] || record.readiness_state)}`}</span></div></div>`).join('') : '<div class="empty-insight">No exports yet.</div>';
  host.innerHTML = `<section class="package-panel" id="bid-section-package"><div class="package-head"><div><p class="eyebrow">SUBMISSION PACKAGE</p><h2>Submission package</h2><p>What will be exported for this bid. Nothing is included automatically.</p></div><div class="package-readiness ${data.readiness.state.toLowerCase()}"><b>${escapeHtml(LABELS.readiness[data.readiness.state] || data.readiness.state)}</b><span>${escapeHtml((data.readiness.reasons || []).join(' '))}</span></div></div><div class="package-controls"><label>Package status<select id="package-status" aria-label="Package status">${statusOptions(data.status, 'package')}</select></label>${canReview ? '<button class="secondary-button" id="mark-package-reviewed">Mark final review done</button>' : ''}${readyToSubmit ? '<button class="secondary-button" id="mark-ready-to-submit">Mark bid ready to submit</button>' : ''}<button class="secondary-button" id="add-package-evidence">Add company evidence…</button></div>${blockers ? `<ul class="blocker-list package-blockers">${blockers}</ul>` : ''}<div id="export-status"></div><div class="package-items">${items}</div><div class="package-actions"><button class="secondary-button" id="export-incomplete">Export incomplete package</button><button class="primary-button" id="export-package">Export package <span>↓</span></button></div><p class="ai-note">READY means every included document is marked READY by you and no blocker remains. It is not a compliance determination. Exporting marks the package as exported locally.</p><div class="package-history"><h4>Export history</h4>${history}</div><div id="preview-panel" class="preview-panel hidden"></div></section>`;
  bindPackageControls(tenderId);
}

function bindPackageControls(tenderId) {
  const reload = () => { loadBidPackage(tenderId); loadOverview(); };
  document.querySelectorAll('.package-included').forEach((box) => box.addEventListener('change', () => api(`/api/bid-package-items/${box.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ included: box.checked }) }).then(reload)));
  document.querySelectorAll('.package-status').forEach((select) => select.addEventListener('change', () => api(`/api/bid-package-items/${select.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: select.value }) }).then(reload)));
  document.querySelectorAll('.package-notes').forEach((input) => input.addEventListener('change', () => api(`/api/bid-package-items/${input.dataset.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: input.value }) })));
  document.querySelectorAll('.package-preview').forEach((button) => button.addEventListener('click', () => openPreview(Number(button.dataset.id))));
  const statusSelect = $('#package-status');
  if (statusSelect) statusSelect.addEventListener('change', () => api(`/api/tenders/${tenderId}/bid-package`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: statusSelect.value }) }).then(reload));
  const markReviewed = $('#mark-package-reviewed');
  if (markReviewed) markReviewed.addEventListener('click', () => api(`/api/tenders/${tenderId}/bid-package`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'REVIEWED' }) }).then(reload));
  const markReady = $('#mark-ready-to-submit');
  if (markReady) markReady.addEventListener('click', () => api(`/api/tenders/${tenderId}/bid-workspace/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflow_status: 'READY_TO_SUBMIT' }) }).then(() => { loadBidWorkspace(tenderId); reload(); }));
  $('#add-package-evidence').addEventListener('click', () => openPicker('package', null, '', tenderId));
  async function doExport(allowIncomplete) {
    const response = await fetch(`/api/tenders/${tenderId}/bid-package/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ allow_incomplete: allowIncomplete }) });
    if (!response.ok) {
      let detail = 'Export failed.';
      try { detail = (await response.json()).detail || detail; } catch (error) { /* keep default */ }
      const exportHost = $('#export-status');
      if (exportHost) {
        exportHost.innerHTML = `<div class="export-error"><strong>Export blocked</strong><p>${escapeHtml(detail)}</p></div>`;
        exportHost.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else {
        window.alert(detail);
      }
      return;
    }
    const blob = await response.blob();
    const anchor = document.createElement('a');
    anchor.href = URL.createObjectURL(blob);
    anchor.download = 'tender_submission_package.zip';
    anchor.click();
    URL.revokeObjectURL(anchor.href);
    await loadBidPackage(tenderId);
  }
  $('#export-package').addEventListener('click', () => doExport(false));
  $('#export-incomplete').addEventListener('click', () => {
    if (window.confirm('This package is incomplete and is not submission-ready. Export anyway?')) doExport(true);
  });
}
/*__WS7__*/





