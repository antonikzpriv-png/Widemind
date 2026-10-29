"""Durable customer dialogue. Model output proposes facts; code owns commitments.

No provider, payment mutation, account configuration or autonomous model loop lives
here. Each new message has at most one budget-authorized model action and one
journaled response. Quote versions remain separate from accepted core orders.
"""
import hashlib
import json
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone

from .conversation_schema import build_prompt, parse_interpretation
from .contracts import Outcome
from .policy import Policy
from .services import text
from .store import encode, identity


def opted_out(body):
    # Suppress contact immediately, even when no model or operating credit exists.
    newest = re.split(r'(?im)^\s*>|^On .{0,300}wrote:\s*$', body, maxsplit=1)[0]
    return bool(re.search(r"(?i)\b(unsubscribe|stop (?:emailing|contacting|messaging)(?: me)?|do not (?:email|contact|message) me|don't (?:email|contact|message) me|remove me from (?:your|the) list)\b", newest)
                or re.fullmatch(r'(?i)\s*(?:please\s+)?(?:stop|opt(?:\s+me)?[\s-]?out)[.!\s]*', newest)
                or re.search(r"(?i)\b(?:i(?:\s+(?:want|wish)|(?:'d|\s+would)\s+like)\s+to\s+opt[\s-]?out|i(?:\s+am|'m)\s+opting[\s-]out|no more (?:e-?mails?|messages?|updates?)|(?:do not|don't|stop)\s+(?:send|sending)\s+(?:me\s+)?(?:(?:any\s+)?more\s+)?(?:e-?mails?|messages?|updates?)|take me off (?:your|the|this) (?:mailing )?list)\b", newest))


class ProposalUnavailable(ValueError):
    """Requirements can be complete while capacity or operator policy forbids work."""


class Conversations:
    def __init__(self, business):
        self.b, self.s = business, business.store

    def thread(self, cid):
        row = self.s.one('SELECT * FROM customer_threads WHERE id=? AND run_id=?', (cid, self.b.run_id))
        if not row:
            raise ValueError('unknown conversation')
        return row

    def _stopped(self, thread):
        return self.b._opted_out(thread['customer_id']) or not self.b._contact_allowed(thread['id'])

    def _stop_contact(self, thread, source):
        # A portal capability proves ownership of one request, not of the email
        # address supplied at public intake. Only verified mail has global scope.
        if source == 'gmail':
            self.s.db.execute('UPDATE customers SET opted_out=1 WHERE id=?', (thread['customer_id'],))
            self.s.db.execute("UPDATE customer_threads SET contact_allowed=0,status='opted_out',intervention=NULL WHERE run_id=? AND customer_id=?", (self.b.run_id, thread['customer_id']))
        else:
            self.s.db.execute("UPDATE customer_threads SET contact_allowed=0,status='opted_out',intervention=NULL WHERE id=?", (thread['id'],))

    def receive(self, *, source, external_id, thread_id, customer_email, body, authenticated,
                opportunity_id=None, reply_reference=None, reply_metadata=None, content_complete=True):
        if authenticated is not True or source not in {'gmail', 'customer-portal'}:
            raise ValueError('authenticated customer transport required')
        body = text(body, 'customer message', 8000)
        if len(body.encode()) > 12000:
            raise ValueError('customer message exceeds byte limit')
        text(external_id, 'message identity', 300); text(thread_id, 'thread identity', 300)
        if source == 'customer-portal':
            external_id = identity(thread_id, external_id)
        with self.s.transaction():
            customer = self.b._customer(customer_email)
            cid = identity(self.b.run_id, 'dialogue', source, thread_id, customer)
            if source == 'customer-portal' and opportunity_id:
                bound = self.s.one('SELECT * FROM customer_threads WHERE run_id=? AND opportunity_id=?', (self.b.run_id, opportunity_id))
                if bound:
                    if bound['customer_id'] != customer:
                        raise ValueError('conversation customer binding changed')
                    cid = bound['id']
            op = opportunity_id or identity(self.b.run_id, 'dialogue-opportunity', cid)
            prior = self.s.one('SELECT * FROM customer_threads WHERE id=?', (cid,))
            if prior and prior['opportunity_id'] != op:
                raise ValueError('conversation request binding changed')
            old_op = self.s.one('SELECT customer_id,run_id FROM opportunities WHERE id=?', (op,))
            if old_op and old_op != {'customer_id': customer, 'run_id': self.b.run_id}:
                raise ValueError('conversation customer binding changed')
            if not old_op:
                self.s.db.execute("INSERT INTO opportunities(id,run_id,customer_id,origin,source_reference,service,scope,priority,status,created_at) VALUES (?,?,?,'inbound',?,'unclassified','{}',1,'conversation',?)",
                                  (op, self.b.run_id, customer, external_id, self.b.clock()))
            self.s.db.execute('INSERT OR IGNORE INTO customer_threads(id,run_id,customer_id,source,thread_id,opportunity_id,last_message_at) VALUES (?,?,?,?,?,?,?)',
                              (cid, self.b.run_id, customer, source, thread_id, op, self.b.clock()))
            if not prior and not self.s.one('SELECT 1 FROM conversation_messages WHERE conversation_id=?', (cid,)):
                self._import_existing(cid, op)
            mid = identity(self.b.run_id, source, external_id, 'dialogue-inbound')
            seen = self.s.one('SELECT * FROM conversation_messages WHERE id=?', (mid,))
            metadata = {'reply_reference': reply_reference} if reply_reference else {}
            if content_complete is not True: metadata['content_incomplete'] = True
            if reply_metadata is not None:
                if not isinstance(reply_metadata, dict) or len(encode(reply_metadata)) > 4000:
                    raise ValueError('invalid reply metadata')
                metadata['reply'] = reply_metadata
            if seen:
                if (seen['conversation_id'], seen['body'], seen['metadata']) != (cid, body, encode(metadata)):
                    raise ValueError('message identity reused with changed content')
                return cid
            self.s.db.execute("INSERT INTO conversation_messages VALUES (?,?,?,?,?,'inbound',?,'queued',NULL,?,?)",
                              (mid, cid, self.b.run_id, source, external_id, body, encode(metadata), self.b.clock()))
            self.s.db.execute('UPDATE customer_threads SET inbound_count=inbound_count+1,revision=revision+1,last_message_at=? WHERE id=?', (self.b.clock(), cid))
            contact = self.thread(cid)
            if opted_out(body) or self._stopped(contact):
                if opted_out(body):
                    self._stop_contact(contact, source)
                self.s.db.execute("UPDATE conversation_messages SET status='opted_out' WHERE id=?", (mid,))
            else:
                self.b._enqueue('conversation', mid, 'received')
        return cid

    def _terms(self, quote_id, version, op, service, scope, price, turnaround, expires, revisions):
        deliverables = {'research-brief': ['One sourced Markdown research brief covering the agreed sections'],
                       'svg-graphic': ['One SVG graphic using the agreed title and description'],
                       'software-starter': ['One static HTML starter source file; no arbitrary code execution'],
                       'custom-graphic': ['Customer-specific checked SVG and PNG originals with private versioned manifests'],
                       'interactive-calculator': ['Functional verified arithmetic calculator, editable source, usage instructions and isolated preview']}
        proposal = {'quoteId': quote_id, 'version': version, 'opportunityId': op,
                    'service': service, 'scope': scope, 'priceMicros': str(price),
                    'deliverables': deliverables.get(service, ['The original explicitly scoped deliverable']),
                    'exclusions': ['Hosting, deployment, subscriptions and third-party account access', 'Additional scope and work beyond the stated revision allowance'],
                    'turnaroundSeconds': turnaround, 'revisionAllowance': revisions, 'expiresAt': expires}
        proposal['digest'] = hashlib.sha256(encode(proposal).encode()).hexdigest()
        return proposal

    def _import_existing(self, cid, op):
        """Continue existing structured intake without rewriting its quote/order."""
        opportunity = self.s.one('SELECT * FROM opportunities WHERE id=? AND run_id=?', (op, self.b.run_id))
        self.s.db.execute('UPDATE customer_threads SET context=? WHERE id=?',
                          (encode({'service': opportunity['service'] if opportunity['service'] in self.b.services else 'unknown', 'scope': json.loads(opportunity['scope'])}), cid))
        quote = self.s.one('SELECT * FROM quotes WHERE opportunity_id=?', (op,))
        if quote:
            proposal = self._terms(quote['id'], 1, op, opportunity['service'], json.loads(quote['scope']),
                                   quote['price_micro_usd'], quote['turnaround_seconds'], quote['expires_at'],
                                   json.loads(quote['scope']).get('max_revisions', 0) if getattr(self.b.services.get(opportunity['service']), 'enhanced', False) else 0)
            state = quote['status'] if quote['status'] in {'offered','accepted'} else 'needs_review'
            self.s.db.execute('INSERT OR IGNORE INTO conversation_proposals VALUES (?,?,?,?,?,?,?)',
                              (quote['id'], cid, 1, proposal['digest'], encode(proposal), quote['plan'], state))
            self.s.db.execute('UPDATE customer_threads SET current_quote_id=?,status=? WHERE id=?',
                              (quote['id'], 'accepted' if state == 'accepted' else 'quoted' if state == 'offered' else 'collecting', cid))
        if opportunity['status'] != 'ordered':
            self.s.db.execute("UPDATE opportunities SET status='conversation' WHERE id=?", (op,))

    def _set_status(self, cid, status, reason=None):
        self.s.db.execute('UPDATE customer_threads SET status=?,intervention=? WHERE id=?', (status, reason, cid))

    def _current(self, thread):
        row = self.s.one('SELECT * FROM conversation_proposals WHERE quote_id=?', (thread['current_quote_id'],))
        return {**json.loads(row['document']), 'status': row['status']} if row else None

    def view(self, cid):
        thread = self.thread(cid)
        proposals = self.s.all('SELECT document,status FROM conversation_proposals WHERE conversation_id=? ORDER BY version DESC LIMIT 50', (cid,))
        return {key: thread[key] for key in ('id', 'customer_id', 'thread_id', 'opportunity_id', 'revision')} | {
            'status': 'opted_out' if self._stopped(thread) else thread['status'],
            'intervention': thread['intervention'], 'operatorHold': bool(thread['operator_hold']), 'currentQuote': self._current(thread),
            'proposals': [{**json.loads(row['document']), 'status': row['status']} for row in proposals],
            'messages': self.s.all('SELECT id,direction,body,status,created_at FROM conversation_messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT 50', (cid,))[::-1]}

    def for_opportunity(self, op):
        row = self.s.one('SELECT id FROM customer_threads WHERE run_id=? AND opportunity_id=?', (self.b.run_id, op))
        return self.view(row['id']) if row else None

    def listing(self):
        result = []
        for row in self.s.all('SELECT * FROM customer_threads WHERE run_id=? ORDER BY last_message_at DESC,id LIMIT 100', (self.b.run_id,)):
            proposal = self._current(row)
            summary = {key: row[key] for key in ('id','customer_id','thread_id','opportunity_id','status','revision','intervention')}
            summary['operatorHold'] = bool(row['operator_hold'])
            if self._stopped(row): summary['status'] = 'opted_out'
            summary['currentQuote'] = {key: proposal[key] for key in ('quoteId','version','priceMicros','service','status','turnaroundSeconds','revisionAllowance','expiresAt')} if proposal else None
            result.append(summary)
        return result

    def _prepare(self, thread, mid, body):
        # Caller holds the write transaction. Any access-token claim and outbound
        # body commit together; an interrupted send reuses this exact body.
        rid = identity(self.b.run_id, mid, 'dialogue-reply')
        original = self.s.one('SELECT source FROM conversation_messages WHERE id=?', (mid,))
        if not original:
            original = self.s.one("SELECT source FROM conversation_messages WHERE conversation_id=? AND direction='inbound' ORDER BY rowid DESC LIMIT 1", (thread['id'],))
        channel = original['source'] if original else thread['source']
        self.s.db.execute("INSERT OR IGNORE INTO conversation_messages VALUES (?,?,?,?,?,'outbound',?,'pending',NULL,'{}',?)",
                          (rid, thread['id'], self.b.run_id, channel, mid, body, self.b.clock()))
        return rid

    def _deliver(self, mid):
        reply = self.s.one("SELECT * FROM conversation_messages WHERE run_id=? AND external_id=? AND direction='outbound'", (self.b.run_id, mid))
        if not reply or reply['status'] in {'accepted','published','suppressed'}:
            return
        thread = self.thread(reply['conversation_id'])
        if self._stopped(thread):
            self.s.db.execute("UPDATE conversation_messages SET status='suppressed' WHERE id=?", (reply['id'],))
            return
        if not self.b._active():
            from .engine import Deferred
            raise Deferred('runtime paused/stopped before conversation response')
        if reply['source'] == 'customer-portal':
            self.b._email(thread['opportunity_id'], 'conversation:'+mid, thread['customer_id'], 'WideMind — your request', reply['body'])
            status = 'published'
        else:
            customer = self.s.one('SELECT email FROM customers WHERE id=?', (thread['customer_id'],))
            original = self.s.one('SELECT metadata FROM conversation_messages WHERE id=?', (mid,))
            if not original:
                original = self.s.one("SELECT metadata FROM conversation_messages WHERE conversation_id=? AND direction='inbound' AND source='gmail' ORDER BY rowid DESC LIMIT 1", (thread['id'],))
            arguments = {'to': customer['email'], 'subject': 'WideMind — your request', 'body': reply['body'], 'thread_id': thread['thread_id']}
            ref = json.loads(original['metadata']).get('reply_reference') if original else None
            reply_headers = json.loads(original['metadata']).get('reply', {}) if original else {}
            for key in ('thread_id','subject','in_reply_to','references'):
                if key in reply_headers: arguments[key] = reply_headers[key]
            if ref and 'in_reply_to' not in arguments:
                arguments['in_reply_to'] = ref
                arguments['references'] = [ref]
            if not arguments.get('in_reply_to') or not arguments.get('references'):
                self._set_status(thread['id'], 'needs_operator', 'Gmail reply identity is incomplete; use the authenticated portal')
                self.s.db.execute("UPDATE conversation_messages SET status='needs_operator' WHERE id=?", (reply['id'],))
                return
            try:
                result = self.b._tool(thread['id'], 'reply:'+mid, 'gmail.send', arguments)
            except Exception:
                if self._stopped(thread):
                    self.s.db.execute("UPDATE conversation_messages SET status='suppressed' WHERE id=?", (reply['id'],))
                    return
                raise
            if result.get('delivery_state') not in {'accepted','confirmed'}:
                from .engine import Deferred
                raise Deferred('Gmail response requires observational reconciliation')
            self.b._record_mail(thread['opportunity_id'], 'conversation:'+mid, thread['customer_id'], result)
            status = 'accepted'  # Provider acceptance is not customer delivery.
        with self.s.transaction():
            self.s.db.execute('UPDATE conversation_messages SET status=? WHERE id=?', (status, reply['id']))
            self.s.db.execute('UPDATE customer_threads SET last_reply_at=? WHERE id=?', (self.b.clock(), thread['id']))

    def _fallback(self, thread, msg, reason):
        with self.s.transaction():
            self._set_status(thread['id'], 'needs_operator', reason)
            self.s.db.execute("UPDATE conversation_messages SET status='applied' WHERE id=?", (msg['id'],))
            self._prepare(thread, msg['id'], 'Thanks for your message. I need an operator to review this before making a commitment. No quote, payment confirmation or account change has been made.')
        self._deliver(msg['id'])

    def process(self, mid):
        msg = self.s.one("SELECT * FROM conversation_messages WHERE id=? AND run_id=? AND direction='inbound'", (mid, self.b.run_id))
        if not msg:
            raise ValueError('unknown conversation message')
        thread = self.thread(msg['conversation_id'])
        if msg['status'] == 'applied':
            self._deliver(mid); return
        if msg['status'] != 'queued' or self._stopped(thread):
            return
        if not self.b._active():
            from .engine import Deferred
            raise Deferred('runtime paused/stopped')
        newest = self.s.one("SELECT id FROM conversation_messages WHERE conversation_id=? AND direction='inbound' ORDER BY rowid DESC LIMIT 1", (thread['id'],))
        if newest['id'] != mid:
            self.s.db.execute("UPDATE conversation_messages SET status='superseded' WHERE id=?", (mid,)); return
        p = self.b.policy
        facts = json.loads(thread['context'])
        if getattr(self.b.services.get(facts.get('service')), 'enhanced', False):
            # Enhanced scope has nested acceptance data that the initial sales
            # interpreter cannot represent. Keep these conversations free and
            # descriptive; only the structured change controls authorize work.
            proposal = self._current(thread)
            response = (self._answer_question(msg['body'], proposal) if proposal else
                        'Custom graphics and calculators require the detailed scope form before quoting.')
            response += ' Use the private delivery controls for included revisions or a separately quoted scope change. A message cannot change the accepted scope, budget or verified payment status.'
            with self.s.transaction():
                if self._stopped(thread):return
                self.s.db.execute("UPDATE conversation_messages SET status='applied' WHERE id=?", (mid,))
                self._prepare(thread, mid, response)
            self._deliver(mid)
            return
        if thread['operator_hold']:
            return self._fallback(thread, msg, thread['intervention'] or 'Operator review is required before new commitments')
        if not p.conversation_enabled or thread['inbound_count'] > p.conversation_max_turns:
            return self._fallback(thread, msg, 'Conversation disabled or per-thread turn limit reached')
        if not self.b.tools.available('ai.generate') or not hasattr(self.b.economy, 'reserve_conversation'):
            return self._fallback(thread, msg, 'Budget-authorized conversation model unavailable')
        if json.loads(msg['metadata']).get('content_incomplete'):
            return self._fallback(thread, msg, 'Message was truncated by the transport; request complete requirements')
        def clipped(value, maximum):
            return value.encode('utf-8')[:maximum].decode('utf-8', errors='ignore')
        facts = json.loads(thread['context'])
        scope_context = {key: clipped(value, 500) if isinstance(value, str) else
                         [clipped(item, 100) for item in value] if isinstance(value, list) else value
                         for key, value in facts.get('scope', {}).items()}
        current_quote = self._current(thread)
        # Send scope once, with explicit excerpts, and no outbound access links.
        # Exact original requirements and proposal documents remain in storage.
        context = {'facts': {'service': facts.get('service','unknown'), 'scope': scope_context},
                   'scope_context_is_excerpt': scope_context != facts.get('scope', {}),
                   'quote': {key: current_quote[key] for key in ('quoteId','version','digest','priceMicros','service','expiresAt','status','turnaroundSeconds','revisionAllowance')} if current_quote else None,
                   'messages': self.s.all("SELECT direction,body FROM conversation_messages WHERE conversation_id=? AND direction='inbound' ORDER BY rowid DESC LIMIT 4", (thread['id'],))[::-1]}
        context['messages'] = [{**item, 'body': clipped(item['body'], 300)} for item in context['messages']]
        try:
            arguments = build_prompt(msg['body'], context, p)
        except ValueError:
            return self._fallback(thread, msg, 'Customer context exceeds the configured model input bound')
        cap = min(p.conversation_model_max_micro_usd, p.max_job_micro_usd)
        if cap <= 0:
            return self._fallback(thread, msg, 'No model spending allowance configured')
        arguments['max_cost_microusd'] = cap
        with self.s.transaction():
            saved = self.s.one('SELECT document FROM settings WHERE run_id=?', (self.b.run_id,))
            policy_current = encode(Policy(**json.loads(saved['document'])).document()) == encode(p.document())
            previous = self.s.one('SELECT * FROM conversation_allocations WHERE message_id=?', (mid,))
            used = self.s.one('SELECT COALESCE(SUM(maximum_micro_usd),0) AS total FROM conversation_allocations WHERE run_id=?', (self.b.run_id,))['total']
            if policy_current and self.b._active() and not previous and used + cap <= p.conversation_run_budget_micro_usd:
                self.s.db.execute('INSERT INTO conversation_allocations VALUES (?,?,?,?)', (mid, self.b.run_id, cap, self.b.clock()))
                previous = {'maximum_micro_usd': cap}
        if not policy_current:
            return self._fallback(thread, msg, 'Operator policy changed before model spending admission')
        if not previous:
            return self._fallback(thread, msg, 'Conversation run spending ceiling reached')
        cap = previous['maximum_micro_usd']
        model_action = identity(self.b.run_id, thread['id'], 'interpret:'+mid)
        reserve_args = {'actual_action_id': model_action, 'maximum_micro_usd': cap, 'tool': 'ai.generate', 'max_tokens': p.conversation_model_max_output_tokens}
        try:
            reservation = self.b._effect(thread['id'], 'reserve:'+mid, 'economy', 'reserve_conversation', reserve_args,
                lambda _aid: self.b.economy.reserve_conversation(run_id=self.b.run_id, conversation_id=thread['id'], action_id=model_action,
                    maximum_micro_usd=cap, max_tokens=p.conversation_model_max_output_tokens))
            result = self.b._tool(thread['id'], 'interpret:'+mid, 'ai.generate', arguments,
                                  reservation=reservation['reservation_id'], maximum=cap)
            interpreted = parse_interpretation(result)
        except (ValueError, KeyError, TypeError):
            return self._fallback(thread, msg, 'Model returned an invalid structured interpretation')
        except Exception:
            self._set_status(thread['id'], 'needs_operator', 'Model or reservation outcome requires observational reconciliation')
            raise
        self._apply(thread, msg, interpreted)
        self._deliver(mid)

    def _questions(self, service, scope):
        if service == 'research-brief':
            questions = []
            if not scope.get('topic'): questions.append('What topic and decision should the research brief address?')
            if not scope.get('sections'): questions.append('Which sections or questions should the brief cover?')
            if not scope.get('min_sources'): questions.append('How many independent source URLs should it include (2–10)?')
            return questions or ['Could you clarify the research requirements so they fit the supported sourced brief?']
        if service in {'svg-graphic','software-starter'}:
            return ['What title and description should the '+('SVG graphic' if service == 'svg-graphic' else 'static HTML starter')+' use?']
        return ['Would you like a sourced research brief, a simple SVG graphic, or a static HTML software starter? What should it accomplish?']

    def _prequote_answer(self, body, service, scope):
        """Answer initial sales questions from policy without inventing a quote."""
        p = self.b.policy
        names = {'research-brief': 'a sourced research brief', 'svg-graphic': 'a simple SVG graphic',
                 'software-starter': 'a static HTML software starter'}
        enabled = [item.name for item in self.b.services.values()
                   if item.capability in p.capabilities and item.name in p.prices]
        selected = [service] if service in enabled else enabled
        question = body.casefold()
        if not selected:
            answer = 'No service is currently enabled for a new quote.'
        elif re.search(r'\b(price|cost|much|charge|budget|rates?)\b', question):
            prices = []
            for name in selected:
                amount = p.prices[name]
                prices.append(f"{names.get(name, name)}: USD {amount//1000000}.{amount%1000000//10000:02d}")
            answer = 'Our configured standard prices are '+ '; '.join(prices)+'.'
        elif re.search(r'\b(when|turnaround|deadline|time|days?|hours?|long)\b', question):
            seconds = p.turnaround_seconds
            turnaround = f'{seconds//3600} hours' if seconds % 3600 == 0 else f'{seconds//60} minutes' if seconds % 60 == 0 else f'{seconds} seconds'
            answer = f'The configured standard turnaround is {turnaround} after verified funding and operating authorization.'
        elif re.search(r'\b(revisions?|changes?|edits?)\b', question):
            answer = f'The standard proposal allowance is {p.conversation_revision_allowance} revisions; the exact allowance will be stated in your quote.'
        elif re.search(r'\b(exclusions?|hosting|deploy|accounts?|subscriptions?)\b', question):
            answer = 'Standard work excludes hosting, deployment, subscriptions and access to third-party accounts.'
        else:
            answer = 'The configured service types are '+ '; '.join(names.get(name, name) for name in selected)+'.'
        answer += ' This is indicative information, not a quote or commitment; exact scope, tool availability, capacity and verified funding still need confirmation.'
        if service in enabled:
            answer += ' To prepare an exact quote, '+ ' '.join(self._questions(service, scope))
        elif enabled:
            answer += ' Which of these supported options should we scope, and what should it accomplish?'
        return answer

    @staticmethod
    def _answer_question(body, proposal):
        question = body.casefold()
        seconds = proposal['turnaroundSeconds']
        turnaround = f'{seconds//3600} hours' if seconds % 3600 == 0 else f'{seconds//60} minutes' if seconds % 60 == 0 else f'{seconds} seconds'
        amount = int(proposal['priceMicros'])
        if re.search(r'\b(paid|payment|invoice|receipt|refund)\b', question):
            answer = 'A message or payment screenshot cannot confirm settlement or authorize a refund. Your private portal uses the payment service’s verified status.'
        elif re.search(r'\b(when|turnaround|deadline|time|days?|hours?)\b', question):
            answer = f'The proposed turnaround is {turnaround} after verified funding and operating authorization. Earlier delivery or additional scope is not included unless a new quote confirms it.'
        elif re.search(r'\b(revisions?|changes?|edits?)\b', question):
            answer = f'This quote includes {proposal["revisionAllowance"]} revisions. Changes after acceptance require review against that allowance before any extra work or charge is agreed.'
        elif re.search(r'\b(exclusions?|hosting|deploy|accounts?|subscriptions?)\b', question):
            answer = 'This quote excludes: '+'; '.join(proposal['exclusions'])+'.'
        elif re.search(r'\b(price|cost|much|charge|budget)\b', question):
            answer = f'The current quoted total is USD {amount//1000000}.{amount%1000000//10000:02d}. Nothing is charged merely by discussing or viewing the quote.'
        else:
            answer = 'The proposed deliverables are: '+'; '.join(proposal['deliverables'])+'. Please tell me which detail you would like clarified.'
        return answer+f' This refers to quote {proposal["quoteId"]}, version {proposal["version"]}; review the complete terms in your private portal before accepting.'

    def _proposal(self, thread, service_name, scope, proposed_price):
        p = self.b.policy
        service = self.b.services.get(service_name)
        if not service or service.capability not in p.capabilities:
            raise ProposalUnavailable('Requested service is not an enabled capability')
        required = ('topic','sections','min_sources') if service_name == 'research-brief' else ('title','description')
        if any(not scope.get(field) for field in required):
            raise ValueError('Requirements need clarification')
        plan = service.plan(scope, p.max_job_micro_usd)
        if not self.b.tools.available(plan.action):
            raise ProposalUnavailable('Required creation capability is unavailable')
        floor = p.conversation_price_floors.get(service_name, p.prices[service_name])
        current = self._current(thread)
        previous_price = int(current['priceMicros']) if current and current['service'] == service_name else p.prices[service_name]
        price = max(floor, min(proposed_price or previous_price, p.prices[service_name]))
        if price % 10000 or price <= plan.maximum_micro_usd or plan.maximum_micro_usd > p.max_job_micro_usd:
            raise ProposalUnavailable('Price or delivery cost violates the operator limits')
        if current and current['scope'] == scope and current['service'] == service_name and current['priceMicros'] == str(price) and current['status'] == 'offered' and current['expiresAt'] > self.b.clock():
            return current
        count = self.s.one("SELECT COUNT(*) AS n FROM orders WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')", (self.b.run_id,))['n']
        if count >= p.max_open_orders:
            raise ProposalUnavailable('Current capacity is full; an operator must confirm availability')
        version = self.s.one('SELECT COALESCE(MAX(version),0)+1 AS n FROM conversation_proposals WHERE conversation_id=?', (thread['id'],))['n']
        proposal = self._terms(identity(self.b.run_id, thread['id'], 'proposal', version), version,
                               thread['opportunity_id'], service_name, scope, price, p.turnaround_seconds,
                               self.b.clock()+p.quote_valid_seconds, p.conversation_revision_allowance)
        self.s.db.execute("UPDATE conversation_proposals SET status='superseded' WHERE conversation_id=? AND status='offered'", (thread['id'],))
        self.s.db.execute("INSERT INTO conversation_proposals VALUES (?,?,?,?,?,?,'offered')", (proposal['quoteId'], thread['id'], version, proposal['digest'], encode(proposal), encode(asdict(plan))))
        self.s.db.execute('UPDATE customer_threads SET current_quote_id=? WHERE id=?', (proposal['quoteId'], thread['id']))
        return {**proposal, 'status': 'offered'}

    def _apply(self, thread, msg, action):
        with self.s.transaction():
            current = self.thread(thread['id'])
            saved = self.s.one('SELECT document FROM settings WHERE run_id=?', (self.b.run_id,))
            if not self.b._active():
                from .engine import Deferred
                raise Deferred('runtime paused/stopped before applying conversation')
            if encode(Policy(**json.loads(saved['document'])).document()) != encode(self.b.policy.document()):
                from .engine import Deferred
                raise Deferred('Operator policy changed; review the cached interpretation before recovery')
            if current['revision'] != thread['revision'] or self._stopped(thread):
                self.s.db.execute("UPDATE conversation_messages SET status='superseded' WHERE id=?", (msg['id'],))
                return
            facts = json.loads(thread['context'])
            intent = action['intent']
            if intent == 'opt_out':
                self._stop_contact(thread, msg['source'])
                self.s.db.execute("UPDATE conversation_messages SET status='opted_out',interpretation=? WHERE id=?", (encode(action), msg['id']))
                return
            accepted = self.s.one('SELECT id FROM orders WHERE run_id=? AND customer_id=? AND quote_id IN (SELECT quote_id FROM conversation_proposals WHERE conversation_id=?)', (self.b.run_id, thread['customer_id'], thread['id']))
            if action['conflict'] or action['requires_operator'] or intent == 'complaint' or accepted and intent in {'change','negotiate','request','accept'}:
                self._set_status(thread['id'], 'needs_operator', 'Conflicting requests, complaint, or change to an accepted commitment requires review')
                self.s.db.execute('UPDATE customer_threads SET operator_hold=1 WHERE id=?', (thread['id'],))
                self.s.db.execute("UPDATE conversation_proposals SET status='needs_review' WHERE conversation_id=? AND status='offered'", (thread['id'],))
                response = 'I have recorded your concern for an operator. The accepted scope and payment status remain unchanged. Please describe the specific change or problem; no refund or extra work has been promised.'
            elif accepted:
                response = 'Your accepted quote remains unchanged. Payment and fulfillment status are shown in your private portal from the verified payment service; saying you paid cannot change that status. Please describe any issue you need help with.'
                self._set_status(thread['id'], 'accepted')
            elif intent == 'accept':
                self._set_status(thread['id'], 'awaiting_acceptance')
                proposal = self._current(thread)
                changed = bool(action['scope'] and any(proposal is None or proposal['scope'].get(key) != value for key,value in action['scope'].items()))
                if changed or action['proposed_price_micro_usd'] and (not proposal or str(action['proposed_price_micro_usd']) != proposal['priceMicros']):
                    self.s.db.execute("UPDATE conversation_proposals SET status='needs_review' WHERE conversation_id=? AND status='offered'", (thread['id'],))
                    self._set_status(thread['id'], 'collecting', 'Acceptance includes changed terms; clarify and re-quote')
                response = 'Please review and explicitly accept the exact current quote version in your authenticated private customer portal. A reply such as “yes” or a payment claim does not confirm changed terms or payment.'
            elif intent == 'unknown':
                self._set_status(thread['id'], 'collecting')
                response = 'Could you clarify whether you are requesting new work, asking about a quote, or reporting a problem? I have not changed any commitment or payment status.'
            else:
                service = action['service'] if action['service'] != 'unknown' else facts.get('service', 'unknown')
                scope = {**facts.get('scope', {}), **(action['scope'] if intent in {'request','clarify','change'} else {})}
                facts = {'service': service, 'scope': scope}
                self.s.db.execute('UPDATE customer_threads SET context=? WHERE id=?', (encode(facts), thread['id']))
                try:
                    if service == 'unknown': raise ValueError('Service requires clarification')
                    proposal = self._proposal(thread, service, scope, action['proposed_price_micro_usd'])
                    self._set_status(thread['id'], 'quoted')
                    amount = int(proposal['priceMicros'])
                    response = (f"Here is quote {proposal['quoteId']}, version {proposal['version']}: USD {amount//1000000}.{amount%1000000//10000:02d}.\n"
                                f"Scope: {encode(scope)}\nDeliverables: {'; '.join(proposal['deliverables'])}.\n"
                                f"Exclusions: {'; '.join(proposal['exclusions'])}.\nTurnaround: {proposal['turnaroundSeconds']} seconds after verified funding and operating authorization.\n"
                                f"Included revisions: {proposal['revisionAllowance']}. Any post-acceptance change is reviewed against this allowance before work.\n"
                                'Please review and accept this exact version in your private portal. A quote or a message saying you paid does not establish payment.')
                    if intent == 'question':
                        response = self._answer_question(msg['body'], proposal)
                    if msg['source'] == 'gmail' and hasattr(self.b, 'conversation_portal_link'):
                        access = self.s.one('SELECT revoked_at FROM host_requests WHERE run_id=? AND opportunity_id=?', (self.b.run_id, thread['opportunity_id']))
                        if not access:
                            link = self.b.conversation_portal_link(thread['id'], secrets.token_urlsafe(32))
                            if link: response += '\nPrivate review and acceptance: '+link
                            else: self._set_status(thread['id'], 'needs_operator', 'Configure the public portal origin before Gmail quote acceptance')
                        elif access['revoked_at']:
                            self._set_status(thread['id'], 'needs_operator', 'Private portal access was revoked; operator review required')
                        else:
                            response += '\nUse the private portal link sent earlier to review and accept this version.'
                    self.b._enqueue('conversation.followup', thread['id'], f"quote:{proposal['version']}", self.b.policy.conversation_followup_seconds)
                except ProposalUnavailable as exc:
                    self._set_status(thread['id'], 'needs_operator', str(exc))
                    response = str(exc)+'. I cannot offer a new commitment now. Your requirements are recorded; no charge, refund or extra work has been promised.'
                except (ValueError, KeyError, TypeError):
                    self._set_status(thread['id'], 'collecting')
                    if intent in {'change','request','clarify'}:
                        self.s.db.execute("UPDATE conversation_proposals SET status='superseded' WHERE conversation_id=? AND status='offered'", (thread['id'],))
                    response = (self._prequote_answer(msg['body'], service, scope) if intent == 'question' else
                                'Thanks — before I can quote this, '+ ' '.join(self._questions(service, scope))+' I will confirm a price and exact scope before any commitment.')
            self.s.db.execute("UPDATE conversation_messages SET status='applied',interpretation=? WHERE id=?", (encode(action), msg['id']))
            self._prepare(thread, msg['id'], response)

    def accept(self, *, conversation_id, quote_id, version, digest, customer_email, evidence_reference):
        text(evidence_reference, 'acceptance evidence', 1000)
        thread = self.thread(conversation_id)
        customer = self.s.one('SELECT email FROM customers WHERE id=?', (thread['customer_id'],))
        if customer_email.lower() != customer['email'] or type(version) is not int or not isinstance(digest, str):
            raise ValueError('authenticated customer and exact quote version required')
        with self.s.transaction():
            thread = self.thread(conversation_id)
            row = self.s.one('SELECT * FROM conversation_proposals WHERE quote_id=? AND conversation_id=?', (quote_id, conversation_id))
            if not row or row['version'] != version or row['digest'] != digest or thread['current_quote_id'] != quote_id or row['status'] not in {'offered','accepted'}:
                raise ValueError('quote terms changed; review the current version')
            terms = json.loads(row['document'])
            existing = self.s.one('SELECT id FROM orders WHERE quote_id=?', (quote_id,))
            if existing:
                return existing['id']
            if thread['operator_hold'] or thread['status'] not in {'quoted','awaiting_acceptance'}:
                raise ValueError('Conversation requires clarification or operator review before acceptance')
            if self.s.one("SELECT 1 FROM conversation_messages WHERE conversation_id=? AND direction='inbound' AND status='queued'", (conversation_id,)):
                raise ValueError('A customer message is awaiting assessment; review the next confirmed quote')
            p = Policy(**json.loads(self.s.one('SELECT document FROM settings WHERE run_id=?', (self.b.run_id,))['document']))
            if self._stopped(thread) or not self.b._active() or terms['expiresAt'] <= self.b.clock():
                raise ValueError('quote expired or customer/runtime not available')
            service = self.b.services[terms['service']]
            plan = service.plan(terms['scope'], p.max_job_micro_usd)
            price = int(terms['priceMicros'])
            if service.capability not in p.capabilities or price < p.conversation_price_floors.get(service.name, p.prices[service.name]) or price > p.prices[service.name] or price <= plan.maximum_micro_usd or encode(asdict(plan)) != row['plan']:
                raise ValueError('operator policy changed; a new quote is required')
            used = self.s.one('SELECT COALESCE(SUM(allowance_micro_usd),0) AS n FROM experiments WHERE run_id=?', (self.b.run_id,))['n']
            used += self.b._discovery_allocated()
            allocated = self.s.one('SELECT allowance_micro_usd FROM experiments WHERE opportunity_id=?', (thread['opportunity_id'],))
            previous_allowance = allocated['allowance_micro_usd'] if allocated else 0
            if used+max(0, plan.maximum_micro_usd-previous_allowance) > p.experiment_budget_micro_usd:
                raise ValueError('run experiment allowance exhausted')
            if not allocated: self.s.db.execute('INSERT INTO experiments VALUES (?,?,?)', (self.b.run_id, thread['opportunity_id'], plan.maximum_micro_usd))
            elif plan.maximum_micro_usd > previous_allowance:
                self.s.db.execute('UPDATE experiments SET allowance_micro_usd=? WHERE opportunity_id=?', (plan.maximum_micro_usd, thread['opportunity_id']))
            core_op = thread['opportunity_id']
            original = self.s.one('SELECT id FROM quotes WHERE opportunity_id=?', (core_op,))
            if original and original['id'] != quote_id:
                # Historical structured quotes stay untouched. The revised accepted
                # version uses a linked internal opportunity, scoped by proposal ID.
                core_op = identity(self.b.run_id, thread['opportunity_id'], 'accepted-version', version)
                self.s.db.execute("INSERT OR IGNORE INTO opportunities(id,run_id,customer_id,origin,source_reference,service,scope,priority,status,created_at) VALUES (?,?,?,'inbound',?,?,?,1,'conversation',?)",
                                  (core_op, self.b.run_id, thread['customer_id'], thread['opportunity_id'], service.name, encode(terms['scope']), self.b.clock()))
            else:
                self.s.db.execute('UPDATE opportunities SET service=?,scope=? WHERE id=?', (service.name, encode(terms['scope']), core_op))
            self.s.db.execute("INSERT OR IGNORE INTO quotes VALUES (?,?,?,?,?,?,?,'offered',?)", (quote_id, core_op, price, plan.maximum_micro_usd, terms['turnaroundSeconds'], terms['expiresAt'], encode(terms['scope']), row['plan']))
            self.b.policy = p
            order = self.b._accept_locked(quote_id, customer_email=customer_email, evidence_reference=evidence_reference)
            self.s.db.execute("UPDATE conversation_proposals SET status='accepted' WHERE quote_id=?", (quote_id,))
            self._set_status(conversation_id, 'accepted')
        return order

    def resolve(self, *, conversation_id, evidence_reference):
        text(evidence_reference, 'operator resolution evidence', 1000)
        thread = self.thread(conversation_id)
        kind = 'conversation-resolution:'+identity(evidence_reference)
        with self.s.transaction():
            if self.s.one('SELECT id FROM memory WHERE run_id=? AND entity_id=? AND kind=?', (self.b.run_id, conversation_id, kind)):
                return {'resolved': True}
            self.s.db.execute("UPDATE customer_threads SET operator_hold=0,status='collecting',intervention=NULL,revision=revision+1 WHERE id=?", (conversation_id,))
            self.s.memory(self.b.run_id, conversation_id, kind, {'evidence': evidence_reference, 'decision': 'review completed; fresh assessment required'}, self.b.clock(), thread['customer_id'])
        return {'resolved': True}

    def followup(self, cid):
        thread = self.thread(cid); p = self.b.policy
        if not self.b._active() or self._stopped(thread) or thread['status'] != 'quoted' or thread['followups_sent'] >= p.conversation_followups:
            return
        proposal = self._current(thread)
        if not proposal or proposal['expiresAt'] <= self.b.clock(): return
        now = datetime.fromtimestamp(self.b.clock(), timezone.utc)
        start, end = p.conversation_quiet_start_hour, p.conversation_quiet_end_hour
        quiet = start != end and (start <= now.hour < end if start < end else now.hour >= start or now.hour < end)
        next_allowed = (thread['last_reply_at'] or thread['last_message_at']) + p.conversation_followup_seconds
        if quiet or self.b.clock() < next_allowed:
            delay = max(3600 if quiet else 1, next_allowed-self.b.clock())
            self.b._enqueue('conversation.followup', cid, f"deferred:{thread['followups_sent']}:{int(self.b.clock())}", delay)
            return
        mid = identity(self.b.run_id, cid, 'followup', thread['followups_sent'])
        with self.s.transaction():
            # Recheck the customer and thread under the lock before publication.
            fresh = self.thread(cid)
            if fresh['revision'] != thread['revision'] or self._stopped(thread): return
            self._prepare(thread, mid, f"Would you like to continue with quote {proposal['quoteId']} version {proposal['version']}? Please use your private portal to review its exact terms. No payment or acceptance has been assumed. You can ask us to stop messages at any time.")
        self._deliver(mid)
        with self.s.transaction():
            self.s.db.execute('UPDATE customer_threads SET followups_sent=followups_sent+1 WHERE id=? AND followups_sent=?', (cid, thread['followups_sent']))
            self.b._enqueue('conversation.followup', cid, f"next:{thread['followups_sent']+1}", p.conversation_followup_seconds)

    def recovered(self, effect):
        """Explicit observational recovery schedules existing intents, not new effects."""
        thread = self.s.one('SELECT id FROM customer_threads WHERE id=? AND run_id=?', (effect['job_id'], self.b.run_id))
        if not thread:
            return
        message = self.s.one("SELECT id FROM conversation_messages WHERE conversation_id=? AND direction='inbound' AND status IN ('queued','applied') ORDER BY rowid DESC LIMIT 1", (thread['id'],))
        if message:
            self.b._enqueue('conversation', message['id'], 'reconciled:'+effect['id'])
        if self.s.one("SELECT 1 FROM conversation_messages WHERE conversation_id=? AND direction='outbound' AND status='pending'", (thread['id'],)):
            self.b._enqueue('conversation.followup', thread['id'], 'reconciled:'+effect['id'])
