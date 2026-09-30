(() => {
  window.smartstoreInquiries = [];
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const status = document.querySelector('#message');
  const originalRender = render;

  function renderPanels() {
    const flagged = allItems.filter(item => item.anomaly_flagged);
    const pending = flagged.filter(item => item.review_status === 'Pending review');
    const inquiryMatches = allItems.flatMap(item => (item.inquiry_signals || []).map(signal => ({item, signal})));
    document.querySelector('#inquiry-total').textContent = inquiryMatches.length;
    document.querySelector('#anomaly-total').textContent = flagged.length;
    document.querySelector('#forecast-gate').hidden = pending.length === 0;
    document.querySelector('#pending-anomaly-count').textContent = pending.length;
    document.querySelector('#inventory-list').hidden = pending.length > 0;
    document.querySelector('#empty-state').hidden = pending.length > 0 || visibleItems.length > 0;
    document.querySelector('#anomaly-model').textContent = allItems[0]?.anomaly_model || 'Rule-based checks';
    document.querySelector('#cluster-model').textContent = allItems[0]?.cluster_model || 'Behavior rules';
    const coldStart = allItems.some(item => item.policy_source === 'Baseline (cold start)');
    document.querySelector('#policy-model').textContent = coldStart ? 'Cold start / feedback heuristic' : 'Feedback-adjusted heuristic';

    const signals = document.querySelector('#signal-list');
    signals.innerHTML = inquiryMatches.length ? inquiryMatches.map(({item, signal}) => `<article class="signal-row"><div><strong>${escapeHtml(item.product)}</strong><span>${escapeHtml(signal.query)}</span></div><p>Matched: ${escapeHtml(signal.matched_terms.join(', '))} <b>+${item.inquiry_uplift}% forecast</b></p><small>Unmet terms: ${escapeHtml(signal.unmatched_terms.join(', ') || 'none')}</small></article>`).join('') : '<div class="empty-state">No inquiry signals applied. Add customer language above.</div>';

    const anomalyList = document.querySelector('#anomaly-list');
    anomalyList.innerHTML = flagged.map(item => {
      const index = allItems.indexOf(item);
      return `<article class="review-row"><div class="review-copy"><div class="review-name"><strong>${escapeHtml(item.product)}</strong><span class="status-tag understock-risk">Flagged record</span></div><p>${(item.anomaly_reasons || []).map(escapeHtml).join(' · ')}</p><small>Review: ${escapeHtml(item.review_status)}</small></div><div class="review-actions"><button type="button" class="button button-outline" data-review="Verified Incident" data-index="${index}">Verified incident</button><button type="button" class="button button-outline" data-review="False Alert" data-index="${index}">False alert</button></div></article>`;
    }).join('');
    document.querySelector('#anomaly-empty').hidden = flagged.length > 0;

    const groups = new Map();
    allItems.forEach(item => {
      const key = item.cluster_id ?? item.cluster_name ?? 'Unassigned';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    document.querySelector('#cluster-list').innerHTML = [...groups.entries()].map(([key, items]) => `<article class="cluster-card"><div class="cluster-head"><span>GROUP ${escapeHtml(key)}</span><small>${items.length} product${items.length === 1 ? '' : 's'}</small></div><h3>${escapeHtml(items[0].cluster_name || 'Behavior segment')}</h3><ul>${items.map(item => `<li><span>${escapeHtml(item.product)}</span><small>${escapeHtml(item.category)} / ${escapeHtml(item.status)}</small></li>`).join('')}</ul></article>`).join('') || '<div class="empty-state">Upload inventory to form product groups.</div>';

    document.querySelector('#policy-list').innerHTML = allItems.map((item, index) => `<article class="policy-row"><div class="policy-product"><strong>${escapeHtml(item.product)}</strong><small>${escapeHtml(item.category)} / ${escapeHtml(item.policy_source)}</small></div><div class="policy-qty"><span>RULE BASELINE</span><strong>${item.baseline_reorder}</strong></div><div class="policy-qty policy-suggested"><span>POLICY SUGGESTION</span><strong>${item.rl_reorder}</strong></div><div class="override-control"><label for="override-${index}">Manual quantity</label><input id="override-${index}" type="number" min="0" step="1" value="${item.rl_reorder}"><button class="button button-dark" type="button" data-policy="override" data-index="${index}">Save override</button></div><button class="button button-outline accept-button" type="button" data-policy="accept" data-index="${index}">Accept ${item.rl_reorder}</button></article>`).join('');
    document.querySelectorAll('[data-review]').forEach(button => button.addEventListener('click', () => saveAnomaly(Number(button.dataset.index), button.dataset.review)));
    document.querySelectorAll('[data-policy]').forEach(button => button.addEventListener('click', () => savePolicy(Number(button.dataset.index), button.dataset.policy)));
  }

  async function refreshScenario() {
    const response = await fetch('/scenario', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({items:allItems, scenario:currentScenario(), inquiries:window.smartstoreInquiries})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not refresh inventory analysis.');
    allItems = result.items;
    render();
  }

  async function saveAnomaly(index, decision) {
    const item = allItems[index];
    const response = await fetch('/review/anomaly', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({item, decision})});
    const result = await response.json();
    if (!response.ok) { status.textContent = result.error || 'Could not save anomaly review.'; return; }
    item.review_status = result.decision;
    status.textContent = `${item.product}: marked ${result.decision}.`;
    render();
  }

  async function savePolicy(index, action) {
    const item = allItems[index];
    const input = document.querySelector(`#override-${index}`);
    const managerQuantity = action === 'accept' ? item.rl_reorder : Number(input.value);
    if (!Number.isInteger(managerQuantity) || managerQuantity < 0) { status.textContent = 'Enter a valid non-negative whole quantity.'; return; }
    const decision = action === 'accept' ? 'Accepted' : 'Manual override';
    const response = await fetch('/review/recommendation', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({item, baseline:item.baseline_reorder, recommendation:item.rl_reorder, manager_quantity:managerQuantity, decision, state:item.feedback_state})});
    const result = await response.json();
    if (!response.ok) { status.textContent = result.error || 'Could not save manager decision.'; return; }
    status.textContent = `${item.product}: ${result.decision.toLowerCase()} quantity ${result.manager_quantity}.`;
    await refreshScenario();
    await loadFeedbackHistory();
  }

  async function loadFeedbackHistory() {
    const response = await fetch('/review/history');
    if (!response.ok) return;
    const result = await response.json();
    const history = document.querySelector('#feedback-list');
    history.innerHTML = result.items.length ? result.items.map(item => `<div class="feedback-entry"><span><strong>${escapeHtml(item.product)}</strong><small>${escapeHtml(item.decision)} / ${escapeHtml(item.created_at.slice(0, 16).replace('T', ' '))}</small></span><span>${item.baseline} → ${item.manager_quantity}</span></div>`).join('') : '<p class="history-empty">No manager decisions recorded yet.</p>';
  }

  document.querySelectorAll('.view-tab').forEach(tab => tab.addEventListener('click', () => {
    document.querySelectorAll('.view-tab').forEach(button => { const active = button === tab; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); });
    document.querySelectorAll('.view-content').forEach(panel => { panel.hidden = panel.id !== tab.dataset.view; });
  }));
  document.querySelector('#open-anomaly-review').addEventListener('click', () => document.querySelector('.view-tab[data-view="anomaly-view"]').click());

  document.querySelector('#apply-inquiries').addEventListener('click', async () => {
    window.smartstoreInquiries = document.querySelector('#inquiry-input').value.split(/[\r\n]+/).map(line => line.trim()).filter(Boolean);
    try {
      await refreshScenario();
      document.querySelector('#inquiry-status').textContent = `${window.smartstoreInquiries.length} inquiry line${window.smartstoreInquiries.length === 1 ? '' : 's'} applied.`;
    } catch (error) { status.textContent = error.message; }
  });
  document.querySelector('#clear-inquiries').addEventListener('click', async () => {
    document.querySelector('#inquiry-input').value = '';
    window.smartstoreInquiries = [];
    try { await refreshScenario(); document.querySelector('#inquiry-status').textContent = 'Demand signals cleared.'; }
    catch (error) { status.textContent = error.message; }
  });

  render = function() { originalRender(); renderPanels(); };
  render();
  loadFeedbackHistory();
})();
