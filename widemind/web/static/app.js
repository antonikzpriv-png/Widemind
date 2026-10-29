/* WideMind's UI reads persisted facts and submits explicit operator commands.
   Credentials live only in this module's memory; customer data never becomes HTML. */
const $ = (selector, parent = document) => parent.querySelector(selector);
const state = { token: '', epoch: 0, data: null, view: 'overview', pending: new Set(), refreshing: false, dialogEpoch: 0 };
const labels = { overview: ['The next useful step.', 'A current view of opportunities, commitments, and measured results.'], opportunities: ['Opportunities', 'Assess what is feasible, review scoped offers, and record actual customer acceptance.'], orders: ['Orders & delivery', 'Keep the agreed work, payment, and delivery obligation visible throughout fulfillment.'], finances: ['Money, with context.', 'Cash received is different from earned revenue. Open commitments and measured costs stay visible.'], recovery: ['Activity & recovery', 'Inspect unresolved effects before retrying. An uncertain outcome is not permission to send or spend again.'], settings: ['The operating boundaries.', 'Configure the business goals and capabilities. Accounts and provider secrets stay in the control plane.'] };

function node(tag, attrs = {}, children = []) {
  const item = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value == null) continue;
    if (name === 'class') item.className = value;
    else if (name === 'text') item.textContent = String(value);
    else if (name.startsWith('on')) item.addEventListener(name.slice(2), value);
    else if (name === 'checked' || name === 'disabled' || name === 'required' || name === 'hidden') item[name] = Boolean(value);
    else item.setAttribute(name, String(value));
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child != null) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return item;
}
function replace(target, ...children) { target.replaceChildren(...children.filter(Boolean)); }
function parse(value, fallback = {}) { if (value && typeof value === 'object') return value; try { return JSON.parse(value) ?? fallback; } catch { return fallback; } }
function list(value) { return Array.isArray(value) ? value : []; }
function human(value) { return String(value || 'unavailable').replace(/[_-]+/g, ' ').replace(/^./, char => char.toUpperCase()); }
function money(value) { return Number.isSafeInteger(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(value / 1_000_000) : 'Unavailable'; }
function when(value) {
  if (value == null || value === '') return 'Not set';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.valueOf()) ? 'Unavailable' : `${new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date)} UTC`;
}
function short(id) { return id ? String(id).slice(0, 10) : '—'; }
function pill(value) { return node('span', { class: 'status-pill', 'data-state': value, text: human(value) }); }
function empty(message) { return node('div', { class: 'empty-state', text: message }); }
function button(label, handler, style = 'quiet', attrs = {}) { return node('button', { type: 'button', class: `button ${style}`, onclick: handler, ...attrs }, label); }
function heading(title, description, action) { return node('div', { class: 'view-heading' }, [node('div', {}, [node('h1', { text: title }), node('p', { text: description })]), action]); }
function metric(label, value, note) { return node('div', { class: 'metric' }, [node('div', { class: 'metric-label', text: label }), node('div', { class: 'metric-value', text: value }), node('div', { class: 'metric-note', text: note })]); }
function code(value) { return node('pre', { class: 'details-code', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }); }
function facts(entries) { return node('dl', { class: 'facts' }, entries.flatMap(([label, value]) => [node('dt', { text: label }), node('dd', {}, value instanceof Node ? [value] : [value == null ? 'Unavailable' : String(value)])])); }
function message(text, kind = '') { const target = $('#console-message'); target.className = `notice ${kind}`; target.textContent = text; target.hidden = !text; }
function customer(id) { return list(state.data?.customers).find(item => item.id === id)?.email || 'Customer record unavailable'; }
function subject(record) { const scope = parse(record.scope); return scope.topic || scope.title || human(record.service); }
function modeBanner(target, mode, profile, usesFixtures) {
  target.hidden = mode === 'live' && profile !== 'provider-test';
  if (profile === 'provider-test') target.textContent = 'Provider test — external calls enabled. Gmail and research providers may perform real actions; payment records remain test transactions, not live revenue.';
  else if (profile === 'demo' || mode === 'demo' || usesFixtures === true) target.textContent = `${human(mode)} mode — explicit fixture records. No real customer, payment, email, or live research is claimed by this demonstration.`;
  else if (mode === 'test') target.textContent = 'Test mode — external effects may be enabled by the configured host. Payment records are test transactions, not live revenue.';
  else target.textContent = 'Execution mode is unavailable. Provider readiness and financial records have not been established.';
}

async function api(path, { method = 'GET', body, authenticated = true } = {}) {
  const epoch = state.epoch;
  const controller = new AbortController();
  state.pending.add(controller);
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, { method, cache: 'no-store', credentials: 'omit', signal: controller.signal,
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(authenticated ? { Authorization: `Bearer ${state.token}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (authenticated && epoch !== state.epoch) throw new Error('STALE_SESSION');
    if (response.status === 401 && authenticated) { lock('The token was not accepted, or this session is no longer authorized.'); throw new Error('STALE_SESSION'); }
    let result;
    try { result = await response.json(); } catch { throw new Error('The server did not return a usable response.'); }
    if (!response.ok) throw new Error(result.error || `The request was not accepted (${response.status}).`);
    return result;
  } catch (error) {
    if (authenticated && epoch !== state.epoch) throw new Error('STALE_SESSION');
    if (error.name === 'AbortError') throw new Error(method === 'GET' ? 'The request timed out. Refresh to try again.' : 'The request outcome is uncertain. Refresh the records before submitting again.');
    throw error;
  } finally { clearTimeout(timeout); state.pending.delete(controller); }
}

async function publicPage() {
  $('#close-dialog').addEventListener('click', () => $('#detail-dialog').close());
  $('#detail-dialog').addEventListener('close', () => { state.dialogEpoch += 1; replace($('#dialog-content')); });
  try {
    const data = await api('/api/v1/public', { authenticated: false });
    const status = $('#public-run-state'); status.textContent = human(data.run_state); status.dataset.state = data.run_state || 'unavailable';
    modeBanner($('#public-mode'), data.mode, data.execution_profile, data.uses_fixtures);
    const finances = data.finances || {};
    replace($('#public-metrics'), metric('Spendable operating credit', money(finances.spendable_micro_usd), 'After reservations and holds'), metric('Customer cash received', money(finances.receipts_micro_usd), 'Verified receipts, before refunds'), metric('Earned revenue', money(finances.earned_micro_usd), 'Tied to fulfilled work'), metric('Operating charges', money(finances.operating_cost_micro_usd), 'Measured within the reported cost boundary'));
    const offers = list(data.offerings);
    replace($('#offerings'), ...(offers.length ? offers.map((offer, index) => {
      const contact = typeof data.contact_email === 'string' && /^[^\s<>@,;\x00-\x1f]+@[^\s<>@,;]+\.[^\s<>@,;]+$/.test(data.contact_email) ? data.contact_email : null;
      const request = contact ? button('Discuss a request →', () => publicRequestDialog(offer, contact), 'small') : node('span', { class: 'faint', text: 'Contact not connected' });
      return node('article', { class: 'offering' }, [node('div', { class: 'offering-num', text: `${String(index + 1).padStart(2, '0')} / ${human(offer.name)}` }), node('h3', { text: human(offer.name) }), node('p', { text: offer.description || 'A scoped service delivered through the configured workflow.' }), node('div', { class: 'offering-bottom' }, [node('span', { class: 'price' }, [money(offer.price_micro_usd), node('small', { text: 'scoped offer' })]), request])]);
    }) : [empty('No services are currently enabled. Available work will appear here when configured.')]));
    renderPublicActivity($('#public-activity'), data.activity);
  } catch (error) {
    $('#public-run-state').textContent = 'Unavailable';
    replace($('#public-metrics'), empty('Verified financial records are unavailable. No balance or earnings are assumed.'));
    replace($('#offerings'), empty('Available work could not be loaded. Please try again later.'));
    replace($('#public-activity'), empty('Public activity is unavailable.'));
    $('#public-error').hidden = false; $('#public-error').textContent = error.message;
  }
}
function publicRequestDialog(offer, contact) {
  const content = openDialog('Prepare an email request', 'Send from your own email account');
  const research = offer.name === 'research-brief';
  const form = node('form');
  const fields = [field('Topic or job title', input('topic', '', { required: true, maxlength: '500' }), null, true)];
  if (research) fields.push(field('Brief sections', textarea('sections', 'Overview\nRisks', { rows: 3, required: true }), 'One section per line; up to eight.', true), field('Minimum sources', input('min_sources', '2', { type: 'number', min: '1', max: '10', step: '1', required: true })), field('Optional source URLs', textarea('source_urls', '', { rows: 3, placeholder: 'https://…' }), 'One HTTPS URL per line; up to ten.', true));
  else fields.push(field('Requested scope · JSON', textarea('custom_scope', '{}', { rows: 5, required: true }), 'Describe the scope accepted by this configured service.', true));
  const result = node('div');
  form.append(node('p', { class: 'financial-note', text: 'This creates a structured email draft addressed to WideMind’s configured inbox. Send it from your own email account. A request is not an accepted quote, payment, or delivery commitment.' }), node('div', { class: 'form-grid section-space' }, fields), node('div', { class: 'form-actions' }, [button('Cancel', () => $('#detail-dialog').close()), node('button', { type: 'submit', class: 'button primary', text: 'Prepare email draft' })]), result);
  form.addEventListener('submit', event => {
    event.preventDefault(); replace(result);
    try {
      let scope;
      if (research) {
        const sections = textLines(form.elements.sections.value), sources = textLines(form.elements.source_urls.value);
        if (!sections.length || sections.length > 8 || new Set(sections).size !== sections.length || sections.some(value => value.length > 100)) throw new Error('Use one to eight unique section headings of up to 100 characters.');
        if (sources.length > 10) throw new Error('Use at most ten source URLs.');
        for (const value of sources) { const parsed = new URL(value); if (parsed.protocol !== 'https:' || parsed.username || parsed.password || /[\s\x00-\x1f\x7f]/.test(value)) throw new Error('Source URLs must be valid HTTPS addresses without credentials.'); }
        const minimum = integerValue(form, 'min_sources', 1); if (minimum > 10) throw new Error('Use at most ten minimum sources.'); if (sources.length && sources.length < minimum) throw new Error('Provide at least the agreed minimum number of source URLs, or leave sources empty for configured research.');
        scope = { topic: form.elements.topic.value.trim(), sections, min_sources: minimum, ...(sources.length ? { source_urls: sources } : {}) };
      } else {
        scope = JSON.parse(form.elements.custom_scope.value);
        if (!scope || typeof scope !== 'object' || Array.isArray(scope)) throw new Error('Requested scope must be a JSON object.');
        scope = { topic: form.elements.topic.value.trim(), ...scope };
      }
      const body = JSON.stringify({ type: 'request', service: offer.name, scope }, null, 2);
      const href = `mailto:${encodeURIComponent(contact)}?subject=${encodeURIComponent(`WideMind request: ${offer.name}`)}&body=${encodeURIComponent(body)}`;
      result.append(node('h3', { class: 'subheading', text: 'Your request draft' }), code(body), node('p', { class: 'form-note', text: `Recipient: ${contact}. Opening a draft does not send it. Keep the JSON body intact so the inbox can understand the requested scope.` }));
      if (href.length <= 18000) result.append(node('div', { class: 'form-actions' }, [node('a', { class: 'button primary', href, text: 'Open email draft →' })]));
      else result.append(dialogNotice('This request is too long for a reliable email link. Copy the JSON above into an email to the displayed recipient.'));
    } catch (error) { result.append(dialogNotice(error.message, 'error')); }
  });
  content.append(form);
}

function renderPublicActivity(target, activities) {
  const records = list(activities);
  replace(target, ...(records.length ? records.map(item => node('div', { class: 'activity-row' }, [node('span', {}, [node('i', { class: 'event-dot', 'aria-hidden': 'true' }), `${human(item.kind)} · ${human(item.service)}`]), node('time', { text: when(item.at_utc_epoch) })])) : [empty('No public completion events have been published yet.')]));
}

function lock(reason = '') {
  state.token = ''; state.data = null; state.epoch += 1; state.refreshing = false;
  for (const controller of state.pending) controller.abort();
  state.pending.clear();
  if ($('#detail-dialog').open) $('#detail-dialog').close();
  replace($('#dialog-content')); replace($('#view')); replace($('#runtime-actions'));
  for (const id of ['run-label', 'updated-at', 'console-mode', 'run-state', 'console-message']) $(`#${id}`).textContent = '';
  $('#console-message').hidden = true;
  $('#dialog-title').textContent = 'Details'; $('#dialog-eyebrow').textContent = 'Private record';
  $('#workspace').hidden = true; $('#login').hidden = false;
  for (const id of ['refresh', 'sign-out', 'run-state']) $(`#${id}`).hidden = true;
  $('#operator-token').value = ''; $('#login-error').textContent = reason; $('#login-error').hidden = !reason;
  $('#operator-token').focus();
}
async function refresh({ focus = false } = {}) {
  if (state.refreshing || !state.token) return;
  state.refreshing = true; const epoch = state.epoch;
  $('#refresh').disabled = true;
  try {
    const data = await api('/api/v1/console');
    if (epoch !== state.epoch) return;
    state.data = data;
    $('#login').hidden = true; $('#workspace').hidden = false;
    for (const id of ['refresh', 'sign-out', 'run-state']) $(`#${id}`).hidden = false;
    $('#run-state').textContent = human(data.runtime_state); $('#run-state').dataset.state = data.runtime_state || 'unavailable';
    $('#run-label').textContent = `Run ${data.run_id || 'unavailable'}`;
    modeBanner($('#console-mode'), data.mode, data.execution_profile, data.uses_fixtures);
    $('#updated-at').textContent = `Refreshed ${new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' }).format(new Date())} UTC`;
    renderRuntime(); renderView(focus);
  } catch (error) {
    if (error.message === 'STALE_SESSION') return;
    if (!state.data) { state.token = ''; $('#login-error').textContent = error.message; $('#login-error').hidden = false; }
    else message(`Refresh failed. Displayed records may be stale. ${error.message}`, 'error');
  } finally { if (epoch === state.epoch) { state.refreshing = false; $('#refresh').disabled = false; } }
}
function renderRuntime() {
  const target = $('#runtime-actions');
  if (!state.data.controls_available) { replace(target, node('span', { class: 'record-sub', text: 'Runtime controls are not configured' })); return; }
  replace(target, ...[['active', 'Resume'], ['paused', 'Pause'], ['stopped', 'Stop']].map(([value, label]) => button(label, () => runtimeDialog(value), value === 'stopped' ? 'danger small' : 'quiet small', { disabled: state.data.runtime_state === value })));
}
function renderView(focus = false) {
  if (!state.data) return;
  for (const tab of $('#console-nav').querySelectorAll('button')) tab.toggleAttribute('aria-current', false);
  $(`[data-view="${state.view}"]`, $('#console-nav')).setAttribute('aria-current', 'page');
  const view = $('#view'); replace(view);
  ({ overview: overviewView, opportunities: opportunitiesView, orders: ordersView, finances: financesView, recovery: recoveryView, settings: settingsView }[state.view] || overviewView)(view);
  if (focus) view.focus({ preventScroll: true });
}
function overviewView(view) {
  const data = state.data, orders = list(data.orders), opportunities = list(data.opportunities), summary = data.summary || {};
  const obligations = orders.filter(order => order.delivery_obligation_open);
  const unresolved = list(data.effects).filter(effect => ['unknown', 'running'].includes(effect.state));
  view.append(heading(...labels.overview, button('Add opportunity +', opportunityDialog, 'primary')));
  view.append(node('div', { class: 'metric-grid' }, [metric('Spendable credit', money(summary.spendable_micro_usd), 'From the configured economy'), metric('Cash received', money(summary.receipts_micro_usd), 'Before refunds and processor fees'), metric('Earned revenue', money(summary.earned_micro_usd), 'Confirmed fulfillment, economy-owned'), metric('Open commitments', String(obligations.length), `${unresolved.length} unresolved external ${unresolved.length === 1 ? 'effect' : 'effects'}`)]));
  const stages = [['Opportunities', opportunities.filter(item => !['completed', 'ordered', 'expired'].includes(item.status)).length], ['Awaiting payment', orders.filter(item => ['accepted', 'awaiting_payment'].includes(item.status)).length], ['In fulfillment', orders.filter(item => ['funded', 'ready', 'delivered', 'payment_hold'].includes(item.status)).length], ['Completed', orders.filter(item => item.status === 'completed').length]];
  const pipeline = node('div', { class: 'pipeline' }, stages.map(([name, count]) => node('div', { class: 'pipeline-step' }, [node('strong', { text: count }), node('span', { text: name })])));
  const left = panel('Delivery commitments', obligations.length ? `${obligations.length} open` : 'No open obligations', orderList(obligations.slice(0, 6)));
  const right = panel('The pipeline', 'Persisted records', node('div', { class: 'panel-body' }, [pipeline, node('p', { class: 'form-note', text: 'Waiting does not invoke paid reasoning. New events and scheduled wakeups advance work when the run permits it.' })]));
  view.append(node('div', { class: 'split-grid' }, [left, right]));
  if (unresolved.length) view.append(node('div', { class: 'notice' }, [`${unresolved.length} external outcomes need attention. `, button('Inspect recovery →', () => { state.view = 'recovery'; renderView(true); }, 'small')]));
}
function panel(title, note, contents) { return node('section', { class: 'panel' }, [node('div', { class: 'panel-heading' }, [node('h2', { text: title }), node('small', { text: note })]), contents]); }
function orderList(orders) { return orders.length ? node('ul', { class: 'record-list' }, orders.map(order => node('li', {}, buttonRecord(subject(order), `${customer(order.customer_id)} · ${order.delivery_due_at ? when(order.delivery_due_at) : 'No funded deadline'}`, order.status, () => orderDialog(order.id))))) : node('div', { class: 'panel-body' }, [empty('No open delivery commitments.')]); }
function buttonRecord(title, subtitle, status, handler) { return node('button', { class: 'record-button', type: 'button', onclick: handler }, [node('span', {}, [node('span', { class: 'record-title', text: title }), node('span', { class: 'record-sub', text: subtitle })]), pill(status)]); }
function table(headers, rows) { return node('div', { class: 'table-wrap', role: 'region', 'aria-label': headers[0], tabindex: '0' }, [node('table', { class: 'data-table' }, [node('thead', {}, [node('tr', {}, headers.map(label => node('th', { scope: 'col', text: label })))]), node('tbody', {}, rows)])]); }
function td(content, className = '') { return node('td', { class: className }, content instanceof Node ? [content] : [String(content ?? '—')]); }
function recordLink(title, sub, action) { return node('button', { class: 'table-button', type: 'button', onclick: action }, [node('span', { class: 'record-title', text: title }), node('span', { class: 'record-sub', text: sub })]); }
function filterBar(placeholder, statuses, onFilter) {
  const input = node('input', { type: 'search', placeholder, 'aria-label': placeholder });
  const select = node('select', { 'aria-label': 'Filter by status' }, [node('option', { value: '', text: 'All statuses' }), ...statuses.map(value => node('option', { value, text: human(value) }))]);
  const count = node('span', { class: 'result-count' });
  const update = () => onFilter(input.value.toLowerCase().trim(), select.value, count);
  input.addEventListener('input', update); select.addEventListener('change', update);
  return { element: node('div', { class: 'filters' }, [input, select, count]), update };
}
function opportunitiesView(view) {
  const records = list(state.data.opportunities), quotes = list(state.data.quotes);
  view.append(heading(...labels.opportunities, button('Add opportunity +', opportunityDialog, 'primary')));
  const results = node('div');
  const filters = filterBar('Search opportunities or customers', [...new Set(records.map(item => item.status))].sort(), (query, status, count) => {
    const filtered = records.filter(item => (!status || item.status === status) && `${subject(item)} ${item.service} ${customer(item.customer_id)}`.toLowerCase().includes(query));
    count.textContent = `${filtered.length} ${filtered.length === 1 ? 'opportunity' : 'opportunities'}`;
    replace(results, filtered.length ? table(['Opportunity', 'Customer', 'Stage', 'Expected cost', 'Quote'], filtered.map(item => {
      const quote = quotes.find(value => value.opportunity_id === item.id), assessment = parse(item.assessment);
      return node('tr', {}, [td(recordLink(subject(item), `${human(item.service)} · ${human(item.origin)} · ${short(item.id)}`, () => opportunityDetails(item))), td(customer(item.customer_id)), td(pill(item.status)), td(money(assessment.expected_cost_micro_usd), 'money'), td(quote ? money(quote.price_micro_usd) : 'Not proposed', 'money')]);
    })) : empty('No opportunities match this view. Add a real request or targeted opportunity to begin.'));
  });
  view.append(filters.element, results); filters.update();
}
function ordersView(view) {
  const records = list(state.data.orders); view.append(heading(...labels.orders));
  const results = node('div');
  const filters = filterBar('Search orders or customers', [...new Set(records.map(item => item.status))].sort(), (query, status, count) => {
    const filtered = records.filter(item => (!status || item.status === status) && `${subject(item)} ${customer(item.customer_id)} ${item.id}`.toLowerCase().includes(query));
    count.textContent = `${filtered.length} ${filtered.length === 1 ? 'order' : 'orders'}`;
    replace(results, filtered.length ? table(['Order', 'Stage', 'Cash received', 'Earned', 'Delivery due'], filtered.map(item => { const payment = parse(item.payment); return node('tr', {}, [td(recordLink(subject(item), `${customer(item.customer_id)} · ${short(item.id)}`, () => orderDialog(item.id))), td(pill(item.status)), td(money(payment.receipts_micro_usd), 'money'), td(money(payment.earned_micro_usd), 'money'), td(when(item.delivery_due_at))]); })) : empty('No orders match this view. Accepted scoped offers become orders.'));
  });
  view.append(filters.element, results); filters.update();
}
function financesView(view) {
  const summary = state.data.summary || {}, orders = list(state.data.orders);
  view.append(heading(...labels.finances));
  view.append(node('div', { class: 'metric-grid' }, [metric('Cash received', money(summary.receipts_micro_usd), 'Verified settlement, not a delivery claim'), metric('Earned revenue', money(summary.earned_micro_usd), 'Recognized by the economy after fulfillment'), metric('Operating charges', money(summary.operating_cost_micro_usd), 'Actual or explicitly provisional costs'), metric('Spendable credit', money(summary.spendable_micro_usd), 'After spending reservations and holds')]));
  const details = [['Opening credit', summary.opening_micro_usd], ['Owner funding', summary.owner_funding_micro_usd], ['Refunds / chargebacks', summary.refunds_micro_usd], ['Processor fees', summary.fees_micro_usd], ['Reserved spending', summary.reserved_micro_usd], ['Customer / refund holds', summary.holds_micro_usd], ['Subsidies', summary.subsidies_micro_usd]];
  view.append(node('div', { class: 'split-grid' }, [panel('Funding and exposure', 'USD · exact micro-USD records', node('div', { class: 'panel-body' }, [facts(details.map(([name, value]) => [name, money(value)]))])), panel('Delivery obligations', `${orders.filter(item => item.delivery_obligation_open).length} open`, node('div', { class: 'panel-body' }, [node('p', { class: 'financial-note', text: 'Receipts for unfinished work may be held or refundable. Cancelling an order does not issue a refund or erase the original costs.' }), node('p', { class: 'financial-note', text: 'Unavailable values are not zero. The economy defines the operating-cost boundary and remains authoritative for every amount.' })]))]));
  const rows = orders.map(order => { const payment = parse(order.payment); return node('tr', {}, [td(recordLink(subject(order), short(order.id), () => orderDialog(order.id))), td(pill(payment.status || 'unavailable')), td(money(payment.receipts_micro_usd), 'money'), td(money(payment.refunds_micro_usd), 'money'), td(money(payment.earned_micro_usd), 'money'), td(order.delivery_obligation_open ? 'Open commitment' : 'No open delivery obligation')]); });
  view.append(node('h2', { class: 'subheading', text: 'Order-level records' }), rows.length ? table(['Order', 'Payment', 'Receipts', 'Refunds', 'Earned', 'Obligation'], rows) : empty('No order-level financial records yet.'));
}
function recoveryView(view) {
  view.append(heading(...labels.recovery));
  const effects = list(state.data.effects).filter(effect => ['unknown', 'running', 'failed', 'planned'].includes(effect.state));
  const effectsContainer = node('div');
  effectsContainer.append(node('h2', { class: 'subheading', text: 'External effects that need attention' }));
  for (const effect of effects) {
    const actions = node('div', { class: 'row-actions' });
    if (['unknown', 'running'].includes(effect.state)) actions.append(button('Reconcile outcome', () => evidenceDialog('Reconcile external outcome', 'This schedules inspection of the provider record. It does not repeat the original action.', async evidence => { await sendEvent('reconcile', effect.id, evidence); }), 'small'));
    if (effect.state === 'failed') actions.append(button('Authorize bounded retry', () => evidenceDialog('Authorize a confirmed-failure retry', 'Only confirmed nonbillable failures or authorization requests can use this retry. Paid work needs a fresh approved plan and budget.', async evidence => { await mutate(`/api/v1/effects/${encodeURIComponent(effect.id)}/retry`, { evidence_reference: evidence }); }), 'small'));
    const record = list(state.data.orders).find(item => item.id === effect.job_id);
    if (record) actions.append(button('Inspect order', () => orderDialog(record.id), 'quiet small'));
    effectsContainer.append(node('article', { class: 'event-block' }, [node('div', { class: 'event-block-head' }, [node('h3', { text: `${human(effect.action)} · ${effect.provider || 'provider'}` }), pill(effect.state)]), node('p', { text: effect.reason || 'Inspect the durable provider record before deciding what to do next.' }), node('span', { class: 'record-sub', text: `Action ${effect.id} · attempt ${Number.isInteger(effect.attempt) ? effect.attempt + 1 : 'unavailable'}` }), actions]));
  }
  if (!effects.length) effectsContainer.append(empty('No unresolved or failed external effects in the returned records.'));
  view.append(effectsContainer);
  const events = list(state.data.events).slice().sort((a, b) => (b.due_at || 0) - (a.due_at || 0));
  view.append(node('h2', { class: 'subheading', text: 'Durable event queue' }), events.length ? table(['Event', 'State', 'Scheduled wakeup', 'Result / blocker'], events.map(event => node('tr', {}, [td(node('span', {}, [node('span', { class: 'record-title', text: human(event.kind) }), node('span', { class: 'record-sub', text: short(event.id) })])), td(pill(event.state)), td(when(event.due_at)), td(event.error || (event.state === 'done' ? 'Recorded' : 'Waiting for the worker'))]))) : empty('No persisted events yet.'));
}
function field(label, input, note, full = false) {
  const id = input.id || `field-${crypto.randomUUID()}`; input.id = id;
  return node('div', { class: `field${full ? ' full' : ''}` }, [node('label', { for: id, text: label }), input, note ? node('small', { text: note }) : null]);
}
function input(name, value = '', attrs = {}) { return node('input', { name, value, ...attrs }); }
function textarea(name, value = '', attrs = {}) { return node('textarea', { name, ...attrs }, value); }
function textLines(value) { return String(value || '').split('\n').map(item => item.trim()).filter(Boolean); }
function integerValue(form, name, min = 0) { const value = Number(form.elements[name].value); if (!Number.isSafeInteger(value) || value < min) throw new Error(`${human(name)} must be a whole number of at least ${min}.`); return value; }
function settingsView(view) {
  const policy = state.data.policy || {};
  view.append(heading(...labels.settings));
  const form = node('form');
  const grids = node('div', { class: 'form-grid' }, [field('Business goals', textarea('goals', list(policy.goals).join('\n'), { required: true, rows: 3 }), 'One goal per line.', true), field('Enabled capabilities', input('capabilities', list(policy.capabilities).join(', ')), 'Comma-separated names. A capability also needs a configured provider.', true), field('Experiment allowance · micro-USD', input('experiment_budget_micro_usd', policy.experiment_budget_micro_usd ?? '', { type: 'number', min: '0', step: '1', required: true }), 'Cumulative experiment ceiling; not cash.'), field('Maximum job cost · micro-USD', input('max_job_micro_usd', policy.max_job_micro_usd ?? '', { type: 'number', min: '0', step: '1', required: true }), '1 USD = 1,000,000 micro-USD.'), field('Delivery turnaround · seconds', input('turnaround_seconds', policy.turnaround_seconds ?? '', { type: 'number', min: '1', step: '1', required: true })), field('Quote validity · seconds', input('quote_valid_seconds', policy.quote_valid_seconds ?? '', { type: 'number', min: '1', step: '1', required: true })), field('Open-order limit', input('max_open_orders', policy.max_open_orders ?? '', { type: 'number', min: '1', step: '1', required: true })), field('Priority weight', input('priority_weight', policy.priority_weight ?? '', { type: 'number', min: '0', step: '1', required: true })), field('Contribution weight', input('margin_weight', policy.margin_weight ?? '', { type: 'number', min: '0', step: '1', required: true })), field('Service prices · micro-USD JSON', textarea('prices', JSON.stringify(policy.prices || {}, null, 2), { required: true, rows: 4 }), 'Only registered, configured work should be enabled.', true), field('Goal-fit weights · JSON', textarea('goal_weights', JSON.stringify(policy.goal_weights || {}, null, 2), { required: true, rows: 3 }), 'Weights by service name.', true)]);
  const outbound = input('allow_outbound', '', { type: 'checkbox', checked: policy.allow_outbound === true });
  form.append(grids, node('label', { class: 'check-field section-space' }, [outbound, 'Allow targeted outbound opportunities']), node('p', { class: 'form-note', text: 'Outbound work still requires an identified recipient, relevance reference, and respect for opt-outs.' }), node('div', { class: 'form-actions' }, [node('button', { type: 'submit', class: 'button primary', text: 'Save operating policy' })]));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    await formAction(form, async () => {
      const next = { ...policy, goals: textLines(form.elements.goals.value), capabilities: form.elements.capabilities.value.split(',').map(value => value.trim()).filter(Boolean), allow_outbound: outbound.checked, prices: JSON.parse(form.elements.prices.value), goal_weights: JSON.parse(form.elements.goal_weights.value) };
      for (const key of ['experiment_budget_micro_usd', 'max_job_micro_usd', 'priority_weight', 'margin_weight']) next[key] = integerValue(form, key);
      for (const key of ['turnaround_seconds', 'quote_valid_seconds', 'max_open_orders']) next[key] = integerValue(form, key, 1);
      await mutate('/api/v1/policy', next); message('Operating policy saved. The worker reloads it before the next event.', 'success');
    });
  });
  const integrations = list(state.data.integrations);
  const integrationList = node('ul', { class: 'integration-list' }, integrations.map(item => node('li', {}, [node('div', { class: 'integration-top' }, [node('h3', { text: item.name || 'Integration' }), pill(item.status || 'unavailable')]), node('p', { text: item.detail || 'No readiness details were provided.' })])));
  view.append(node('div', { class: 'settings-grid' }, [panel('Business policy', 'Operator-owned configuration', node('div', { class: 'panel-body' }, [form])), panel('Accounts & integrations', 'Configured host adapters', node('div', { class: 'panel-body' }, [integrations.length ? integrationList : empty('Integration readiness has not been reported.'), node('p', { class: 'financial-note', text: 'Provider credentials are configured in the host environment. They never belong in a customer message, a public activity entry, or this browser form.' })]))]));
}

function openDialog(title, eyebrow = 'Private operations') {
  state.dialogEpoch += 1;
  const dialog = $('#detail-dialog'); $('#dialog-title').textContent = title; $('#dialog-eyebrow').textContent = eyebrow;
  replace($('#dialog-content')); if (!dialog.open) dialog.showModal();
  $('#dialog-content').scrollTop = 0; return $('#dialog-content');
}
function closeFormDialog(form) { if (form.isConnected && $('#detail-dialog').contains(form)) $('#detail-dialog').close(); }
function dialogNotice(text, kind = '') { return node('p', { class: `notice dialog-message ${kind}`, role: 'status', text }); }
async function formAction(form, action) {
  const epoch = state.epoch;
  const submits = [...form.querySelectorAll('button[type=submit]')]; submits.forEach(item => { item.disabled = true; });
  const old = form.querySelector('.dialog-message'); if (old) old.remove();
  try { await action(); }
  catch (error) { if (error.message !== 'STALE_SESSION' && epoch === state.epoch) form.append(dialogNotice(error.message, 'error')); }
  finally { if (epoch === state.epoch) submits.forEach(item => { item.disabled = false; }); }
}
async function mutate(path, payload) { const result = await api(path, { method: 'POST', body: payload }); await refresh(); return result; }
function opportunityDialog() {
  const content = openDialog('Add a real opportunity', 'Request intake');
  const form = node('form'), services = Object.keys(state.data.policy?.prices || {});
  const service = node('select', { name: 'service', required: true }, services.map(name => node('option', { value: name, text: human(name) })));
  const origin = node('select', { name: 'origin' }, ['inbound', 'discovered', 'outbound'].map(name => node('option', { value: name, text: human(name) })));
  form.append(node('div', { class: 'form-grid' }, [field('Service', service), field('Origin', origin), field('Customer email', input('customer_email', '', { type: 'email', autocomplete: 'off' }), 'Leave blank only if the customer is genuinely not known.', true), field('Topic or job title', input('topic', '', { required: true, maxlength: '500' }), null, true), field('Research sections', textarea('sections', 'Overview\nRisks', { rows: 3 }), 'One section per line for research briefs.', true), field('Research source URLs', textarea('source_urls', '', { rows: 3, placeholder: 'https://…' }), 'Optional. One HTTPS source URL per line; up to 10.', true), field('Minimum research sources', input('min_sources', '2', { type: 'number', min: '1', max: '10', step: '1' })), field('Priority · 0–100', input('priority', '1', { type: 'number', min: '0', max: '100', step: '1', required: true })), field('Source or relevance reference', input('source_reference', '', { maxlength: '2048' }), 'Outbound and discovered opportunities require a concrete HTTPS reference.', true), field('Custom scope · JSON', textarea('custom_scope', '', { rows: 3, placeholder: '{ "topic": "…" }' }), 'Optional scope override for other registered services. Do not put credentials here.', true)]));
  const requestId = crypto.randomUUID();
  form.append(node('div', { class: 'form-actions' }, [button('Cancel', () => $('#detail-dialog').close()), node('button', { type: 'submit', class: 'button primary', text: 'Record opportunity' })]));
  form.addEventListener('submit', event => { event.preventDefault(); formAction(form, async () => {
    const values = form.elements;
    let scope;
    if (values.custom_scope.value.trim()) { scope = JSON.parse(values.custom_scope.value); if (!scope || typeof scope !== 'object' || Array.isArray(scope)) throw new Error('Custom scope must be a JSON object.'); }
    else { scope = { topic: values.topic.value.trim(), sections: textLines(values.sections.value), min_sources: integerValue(form, 'min_sources', 1) }; const urls = textLines(values.source_urls.value); if (urls.length) { if (urls.length < scope.min_sources) throw new Error('Source URLs must meet the minimum source count.'); scope.source_urls = urls; } }
    const payload = { source: 'operator-console', external_id: requestId, service: values.service.value, scope, origin: values.origin.value, priority: integerValue(form, 'priority'), source_reference: values.source_reference.value.trim(), customer_email: values.customer_email.value.trim() || null };
    await mutate('/api/v1/opportunities', payload); closeFormDialog(form); message('Opportunity recorded. The independent worker will assess it when the run permits work.', 'success');
  }); });
  content.append(form);
}
function opportunityDetails(opportunity) {
  const content = openDialog(subject(opportunity), 'Opportunity');
  const quote = list(state.data.quotes).find(item => item.opportunity_id === opportunity.id), assessment = parse(opportunity.assessment);
  content.append(facts([['Stage', pill(opportunity.status)], ['Service', human(opportunity.service)], ['Customer', customer(opportunity.customer_id)], ['Origin', human(opportunity.origin)], ['Priority', opportunity.priority], ['Source reference', opportunity.source_reference || 'Not supplied'], ['Record', opportunity.id]]));
  content.append(node('h3', { class: 'subheading', text: 'Agreed or proposed scope' }), code(parse(opportunity.scope)));
  content.append(node('h3', { class: 'subheading', text: 'Feasibility assessment' }), assessment.reason ? dialogNotice(assessment.reason) : code(assessment));
  if (quote) {
    content.append(node('h3', { class: 'subheading', text: 'Scoped quote' }), facts([['Quote', quote.id], ['Status', pill(quote.status)], ['Price', money(quote.price_micro_usd)], ['Cost ceiling', money(quote.cost_micro_usd)], ['Expires', when(quote.expires_at)], ['Turnaround', Number.isFinite(quote.turnaround_seconds) ? `${quote.turnaround_seconds} seconds after verified funding` : 'Unavailable']]));
    if (quote.status === 'offered') content.append(node('div', { class: 'form-actions' }, [button('Record customer acceptance', () => acceptanceDialog(opportunity, quote), 'primary')]));
  }
}
function acceptanceDialog(opportunity, quote) {
  const content = openDialog('Record customer acceptance', 'A real customer decision');
  const form = node('form');
  form.append(node('p', { class: 'financial-note', text: 'Record the customer’s actual acceptance of this exact quote. This does not assert payment, authorize a charge, or manufacture a customer response.' }), facts([['Quote', quote.id], ['Price', money(quote.price_micro_usd)], ['Scope', subject(opportunity)]]), node('div', { class: 'form-grid section-space' }, [field('Matching customer email', input('customer_email', customer(opportunity.customer_id), { type: 'email', required: true }), null, true), field('Acceptance evidence reference', input('evidence_reference', '', { required: true, maxlength: '1000', placeholder: 'Actual message or signed agreement reference' }), null, true)]), node('div', { class: 'form-actions' }, [button('Cancel', () => $('#detail-dialog').close()), node('button', { type: 'submit', class: 'button primary', text: 'Record acceptance' })]));
  form.addEventListener('submit', event => { event.preventDefault(); formAction(form, async () => { await mutate('/api/v1/accept', { quote_id: quote.id, customer_email: form.elements.customer_email.value.trim(), evidence_reference: form.elements.evidence_reference.value.trim() }); closeFormDialog(form); message('Acceptance recorded. Payment status and spending authorization still come from the economy.', 'success'); }); });
  content.append(form);
}
async function orderDialog(id) {
  const content = openDialog(`Order ${short(id)}`, 'Private customer work');
  content.append(node('div', { class: 'loading-indicator', text: 'Loading the private order record…' }));
  const epoch = state.epoch, dialogEpoch = state.dialogEpoch;
  try {
    const order = await api(`/api/v1/orders/${encodeURIComponent(id)}`);
    if (epoch !== state.epoch || dialogEpoch !== state.dialogEpoch || !$('#detail-dialog').open) return;
    $('#dialog-title').textContent = subject(order);
    const scope = parse(order.scope), payment = parse(order.payment), artifact = parse(order.artifact, null);
    replace(content, facts([['Order', order.id], ['Customer', customer(order.customer_id)], ['Status', pill(order.status)], ['Delivery due', when(order.delivery_due_at)], ['Open delivery obligation', order.delivery_obligation_open ? 'Yes' : 'No'], ['Cash received', money(payment.receipts_micro_usd)], ['Refunded', money(payment.refunds_micro_usd)], ['Earned revenue', money(payment.earned_micro_usd)], ['Payment', pill(payment.status || 'unavailable')], ['Delivery reference', order.delivery_reference || 'Not confirmed']]));
    content.append(node('h3', { class: 'subheading', text: 'Agreed scope' }), code(scope));
    const actions = node('div', { class: 'form-actions' });
    if (!['completed', 'cancelled', 'failed'].includes(order.status)) actions.append(button('Schedule a wakeup', () => evidenceDialog('Schedule an order wakeup', 'This queues an event. The worker still checks runtime state, payment, capabilities, and budget before taking an action.', async evidence => { await sendEvent('advance', order.id, evidence); }), 'small'));
    if (order.status === 'payment_hold') { actions.append(button('Resume after verified settlement', () => resolveDialog(order.id, 'resume'), 'small')); actions.append(button('Record cancellation', () => resolveDialog(order.id, 'cancel'), 'danger small')); }
    content.append(actions);
    if (artifact && typeof artifact.content === 'string') {
      content.append(node('h3', { class: 'subheading', text: 'Private deliverable' }), facts([['Filename', artifact.filename], ['Media type', artifact.media_type], ['Scope checks', JSON.stringify(artifact.checks || {})]]), code(artifact.content), node('div', { class: 'form-actions' }, [button('Download private deliverable ↓', () => downloadArtifact(artifact), 'primary small')]));
    } else content.append(dialogNotice('No checked deliverable has been persisted yet.'));
  } catch (error) { if (error.message !== 'STALE_SESSION' && epoch === state.epoch && dialogEpoch === state.dialogEpoch && $('#detail-dialog').open) replace(content, dialogNotice(error.message, 'error')); }
}
function downloadArtifact(artifact) {
  if (!state.token || typeof artifact.content !== 'string') return;
  const blob = new Blob([artifact.content], { type: typeof artifact.media_type === 'string' ? artifact.media_type : 'text/plain' });
  const url = URL.createObjectURL(blob);
  const link = node('a', { href: url, download: String(artifact.filename || 'deliverable.txt').replace(/[\x00-\x1f\x7f/\\]/g, '_').slice(0, 150) });
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function evidenceDialog(title, description, action) {
  const content = openDialog(title), form = node('form');
  form.append(node('p', { class: 'financial-note', text: description }), node('div', { class: 'section-space' }, [field('Evidence / operator decision reference', input('evidence', '', { required: true, maxlength: '1000', autocomplete: 'off' }), 'Use an actual record reference or a clear operator decision.')]), node('div', { class: 'form-actions' }, [button('Cancel', () => $('#detail-dialog').close()), node('button', { type: 'submit', class: 'button primary', text: 'Record and schedule' })]));
  form.addEventListener('submit', event => { event.preventDefault(); formAction(form, async () => { await action(form.elements.evidence.value.trim()); closeFormDialog(form); message('Command recorded. Refresh to follow the persisted outcome.', 'success'); }); });
  content.append(form);
}
async function sendEvent(kind, id, evidence) { return mutate('/api/v1/events', { source: 'operator-console', external_id: crypto.randomUUID(), kind, payload: { id, evidence_reference: evidence } }); }
function resolveDialog(id, decision) { evidenceDialog(decision === 'resume' ? 'Resume a payment hold' : 'Cancel this delivery', decision === 'resume' ? 'The server requires verified settlement and reconciliation of existing effects. The original deadline is preserved.' : 'Cancellation does not refund money. Unrefunded receipts and costs remain visible for economy/operator resolution.', evidence => mutate('/api/v1/resolve-hold', { order_id: id, decision, evidence_reference: evidence })); }
function runtimeDialog(value) { evidenceDialog(`${human(value)} the run`, value === 'stopped' ? 'Stop new work and request cancellation through the configured runtime. Reconciliation remains available. Provider cancellation depends on the host adapter.' : value === 'paused' ? 'Pause new business actions. Keep existing commitments and reconciliation records intact.' : 'Permit work within the existing capability, budget, and account configuration.', evidence => mutate('/api/v1/runtime', { state: value, evidence_reference: evidence, request_id: crypto.randomUUID() })); }

function consolePage() {
  $('#login-form').addEventListener('submit', async event => {
    event.preventDefault(); const field = $('#operator-token'); const token = field.value; field.value = '';
    if (token.length < 32) return;
    state.epoch += 1; state.token = token; state.data = null;
    $('#login-error').hidden = true;
    const submit = $('#login-form button[type=submit]'); submit.disabled = true;
    try { await refresh(); } finally { submit.disabled = false; }
  });
  $('#sign-out').addEventListener('click', () => lock());
  $('#refresh').addEventListener('click', () => refresh());
  $('#console-nav').addEventListener('click', event => { const tab = event.target.closest('[data-view]'); if (tab) { state.view = tab.dataset.view; message(''); renderView(true); } });
  $('#close-dialog').addEventListener('click', () => $('#detail-dialog').close());
  $('#detail-dialog').addEventListener('close', () => { state.dialogEpoch += 1; replace($('#dialog-content')); $('#dialog-title').textContent = 'Details'; $('#dialog-eyebrow').textContent = 'Private record'; });
  window.addEventListener('pagehide', () => lock());
}
if (document.body.dataset.page === 'public') publicPage();
if (document.body.dataset.page === 'console') consolePage();
