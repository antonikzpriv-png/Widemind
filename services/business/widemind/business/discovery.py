"""Event-driven discovery and one-attempt experiments, using existing authorities.

This module stores plans, evidence and conservative allocation limits. It never
owns cash, provider credentials, a scheduler thread or permission to contact an
arbitrary person. A matching sentence is evidence of text, not verified demand.
"""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
import re

from .discovery_policy import configuration, experiment_spec, source_url
from .contracts import Outcome
from .policy import Policy, integer
from .services import single_line, text
from .store import encode, identity


class DiscoveryMixin:
    def _discovery_config(self):
        row = self.store.one('SELECT document FROM discovery_settings WHERE run_id=?', (self.run_id,))
        return configuration(json.loads(row['document']) if row else {}, self.mode)

    def _discovery_allocated(self):
        return self.store.one('SELECT COALESCE(SUM(amount_micro_usd),0) AS n FROM discovery_allocations WHERE run_id=?',
                              (self.run_id,))['n']

    def _discovery_allocate(self, entity, maximum):
        """Called under the business write transaction; never recycles an allowance."""
        config = self._discovery_config()
        if not config['enabled']:
            raise ValueError('discovery is disabled')
        saved_policy = self.store.one('SELECT document FROM settings WHERE run_id=?', (self.run_id,))
        policy = Policy(**json.loads(saved_policy['document']))
        old = self.store.one('SELECT amount_micro_usd FROM discovery_allocations WHERE entity_id=? AND run_id=?',
                             (entity, self.run_id))
        if old:
            if old['amount_micro_usd'] != maximum:
                raise ValueError('work allocation is immutable')
            return
        existing = self.store.one('SELECT COALESCE(SUM(allowance_micro_usd),0) AS n FROM experiments WHERE run_id=?',
                                  (self.run_id,))['n']
        allocated = self._discovery_allocated()
        if maximum > policy.max_job_micro_usd or allocated + maximum > config['allowance_micro_usd'] or existing + allocated + maximum > policy.experiment_budget_micro_usd:
            raise ValueError('discovery allocation exhausted; an operator must review the lifetime allowance')
        self.store.db.execute('INSERT INTO discovery_allocations VALUES (?,?,?,?,?)',
                              (identity(self.run_id, entity, 'allocation'), self.run_id, entity, maximum, self.clock()))

    def configure_discovery(self, document):
        config = configuration(document, self.mode)
        revision = identity(config)
        with self.store.transaction():
            self.store.db.execute('INSERT INTO discovery_settings VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET document=excluded.document,revision=excluded.revision',
                                  (self.run_id, encode(config), revision))
            for market in config['markets']:
                self.store.db.execute("INSERT OR IGNORE INTO discovery_markets(run_id,market_id,state) VALUES (?,?,'configured')",
                                      (self.run_id, market['id']))
            self.store.memory(self.run_id, revision, 'discovery_configuration', config, self.clock())
        if config['enabled']:
            for market in config['markets']:
                self.schedule_discovery(market['id'], 'configuration:' + revision)
            self._schedule_discovery_observation()
        return {'saved': True, 'revision': revision, 'config': config}

    def _discovery_source_reference(self, value):
        config = self._discovery_config()
        if self.mode != 'test' or not config['fixture_mode']:
            return False
        try:
            clean = source_url(value, fixture_mode=True)
            return clean in {url for market in config['markets'] for url in market['source_urls']}
        except (ValueError, TypeError, AttributeError):
            return False

    def schedule_discovery(self, market_id, request_id):
        single_line(request_id, 'discovery request id', 300)
        config = self._discovery_config()
        market = next((m for m in config['markets'] if m['id'] == market_id), None)
        if not config['enabled'] or market is None:
            raise ValueError('market discovery is disabled or unavailable')
        revision = identity(config)
        with self.store.transaction():
            saved = self.store.one('SELECT * FROM discovery_markets WHERE run_id=? AND market_id=?', (self.run_id, market_id))
            pending = self.store.one("SELECT ordinal FROM discovery_attempts WHERE run_id=? AND market_id=? AND state IN ('running','unknown') ORDER BY ordinal LIMIT 1", (self.run_id, market_id))
            ordinal = pending['ordinal'] if pending else saved['attempt_count'] + 1
            if ordinal > market['max_attempts']:
                self.store.db.execute("UPDATE discovery_markets SET state='attempt_limit',next_due_at=NULL WHERE run_id=? AND market_id=?", (self.run_id, market_id))
                return {'scheduled': False, 'reason': 'attempt limit reached'}
            due = max(self.clock(), (saved['last_attempt_at'] or 0) + market['cooldown_seconds'])
            if request_id.startswith('automatic:'):
                due = max(due, self.clock() + market['interval_seconds'])
            key = identity(market_id, ordinal, revision, request_id)
            event_id = self.notify(source='discovery-schedule', external_id=key, kind='discovery.run',
                payload={'market_id': market_id, 'ordinal': ordinal, 'revision': revision}, due_at=due)
            self.store.db.execute("UPDATE discovery_markets SET state='scheduled',next_due_at=? WHERE run_id=? AND market_id=?", (due, self.run_id, market_id))
        return {'scheduled': True, 'event_id': event_id, 'due_at': due}

    def _authorized_discovery_tool(self, entity, step, action, arguments, maximum, table):
        from .engine import Deferred
        if not self._active() or not self.tools.available(action):
            raise Deferred('runtime or configured capability does not permit discovery work')
        if maximum > self.policy.max_job_micro_usd:
            raise Deferred('configured per-job allowance was reduced')
        tool_action = identity(self.run_id, entity, step)
        def revalidate_dispatch(force=False):
            prior = self.store.one('SELECT state FROM effects WHERE logical_id=? ORDER BY attempt DESC LIMIT 1', (tool_action,))
            if not force and prior and prior['state'] != 'planned':
                return  # Only the existing effect's observational recovery remains.
            config = self._discovery_config()
            allocated = self._discovery_allocated()
            jobs = self.store.one('SELECT COALESCE(SUM(allowance_micro_usd),0) AS n FROM experiments WHERE run_id=?', (self.run_id,))['n']
            saved_policy = self.store.one('SELECT document FROM settings WHERE run_id=?', (self.run_id,))
            policy = json.loads(saved_policy['document'])
            if not config['enabled'] or allocated > config['allowance_micro_usd'] or allocated + jobs > policy['experiment_budget_micro_usd'] or maximum > policy['max_job_micro_usd']:
                raise Deferred('operator reduced discovery or inherited spending limits before dispatch')
            if table == 'discovery_attempts':
                attempt = self.store.one('SELECT market_id,market_document FROM discovery_attempts WHERE id=? AND run_id=?', (entity, self.run_id))
                current_market = next((m for m in config['markets'] if m['id'] == attempt['market_id']), None) if attempt else None
                if not attempt or current_market != json.loads(attempt['market_document']) or 'research' not in policy['capabilities']:
                    raise Deferred('research market, source or capability changed before dispatch')
            if table == 'discovery_experiments':
                current = self.store.one('SELECT state,deadline FROM discovery_experiments WHERE id=? AND run_id=?', (entity, self.run_id))
                if not current or current['state'] != 'running' or current['deadline'] <= self.clock():
                    raise Deferred('experiment was stopped or reached its deadline before dispatch')
                opportunity = self.store.one('SELECT o.service FROM opportunities o JOIN discovery_experiments e ON e.opportunity_id=o.id WHERE e.id=?', (entity,))
                service = self.services.get(opportunity['service']) if opportunity else None
                if not service or service.capability not in policy['capabilities']:
                    raise Deferred('experiment service capability was disabled before dispatch')
        revalidate_dispatch()
        request = {'tool_action_id': tool_action, 'tool': action, 'maximum_micro_usd': maximum}
        reserved = self._effect(entity, 'work-reserve', 'economy', 'reserve_work', request,
            lambda aid: self.economy.reserve_work(run_id=self.run_id, job_id=entity, action_id=aid, **request))
        reservation = single_line(reserved.get('reservation_id'), 'reservation id', 200)
        self.store.db.execute('UPDATE ' + table + ' SET reservation_id=? WHERE id=? AND run_id=?', (reservation, entity, self.run_id))
        if not self._active() or not self.tools.available(action):
            raise Deferred('runtime or capability changed after budget authorization')
        revalidate_dispatch()
        def invoke(action_id):
            try:
                if not self._active() or not self.tools.available(action):
                    raise Deferred('runtime or capability changed before dispatch')
                # _effect journals this new dispatch before the callback. The
                # trusted local guard runs before tools.execute is ever called.
                revalidate_dispatch(force=True)
            except Deferred as exc:
                release_uncertain = False
                try:
                    self._effect(entity, 'release-unused', 'economy', 'release', {'reservation_id': reservation},
                        lambda aid: self.economy.release(run_id=self.run_id, job_id=entity, action_id=aid, reservation_id=reservation))
                except Deferred:
                    release_uncertain = True
                return Outcome('failed', {'not_dispatched': True, 'reservation_release_uncertain': release_uncertain}, str(exc))
            return self.tools.execute(run_id=self.run_id, job_id=entity, action_id=action_id,
                action=action, arguments=arguments, reservation_id=reservation, maximum_micro_usd=maximum)
        return self._effect(entity, step, 'tools', action,
            {'arguments': arguments, 'reservation': reservation, 'maximum': maximum}, invoke)

    def _discovery_research(self, payload):
        from .engine import Deferred
        config = self._discovery_config()
        market = next((m for m in config['markets'] if m['id'] == payload.get('market_id')), None)
        if not config['enabled'] or market is None or payload.get('revision') != identity(config):
            return
        ordinal = integer(payload.get('ordinal'), 'discovery ordinal', 1, 100)
        attempt_id = identity(self.run_id, market['id'], 'research', ordinal)
        attempt = self.store.one('SELECT * FROM discovery_attempts WHERE id=? AND run_id=?', (attempt_id, self.run_id))
        if not attempt:
            available = self.tools.available('research.fetch')
            if not available:
                self.store.db.execute("UPDATE discovery_markets SET state='unavailable',next_due_at=NULL WHERE run_id=? AND market_id=?", (self.run_id, market['id']))
                raise Deferred('research.fetch unavailable; configure tools and explicitly wake discovery')
            with self.store.transaction():
                saved = self.store.one('SELECT * FROM discovery_markets WHERE run_id=? AND market_id=?', (self.run_id, market['id']))
                if ordinal != saved['attempt_count'] + 1 or ordinal > market['max_attempts']:
                    return
                if saved['last_attempt_at'] and saved['last_attempt_at'] + market['cooldown_seconds'] > self.clock():
                    return
                if not self._active():
                    raise Deferred('runtime paused/stopped')
                try:
                    self._discovery_allocate(attempt_id, market['research_allowance_micro_usd'])
                except ValueError:
                    self.store.db.execute("UPDATE discovery_markets SET state='budget_exhausted',next_due_at=NULL WHERE run_id=? AND market_id=?", (self.run_id, market['id']))
                    return
                self.store.db.execute("INSERT INTO discovery_attempts(id,run_id,market_id,ordinal,source_url,market_document,state,created_at) VALUES (?,?,?,?,?,?,'running',?)",
                    (attempt_id, self.run_id, market['id'], ordinal, market['source_urls'][(ordinal - 1) % len(market['source_urls'])], encode(market), self.clock()))
                self.store.db.execute("UPDATE discovery_markets SET attempt_count=?,last_attempt_at=?,next_due_at=NULL,state='researching' WHERE run_id=? AND market_id=?",
                                      (ordinal, self.clock(), self.run_id, market['id']))
            attempt = self.store.one('SELECT * FROM discovery_attempts WHERE id=?', (attempt_id,))
        if attempt['state'] == 'completed':
            return
        market = json.loads(attempt['market_document'])
        try:
            result = self._authorized_discovery_tool(attempt_id, 'discovery-fetch', 'research.fetch',
                {'url': attempt['source_url'], 'max_text_chars': 12000, 'max_links': 0}, market['research_allowance_micro_usd'], 'discovery_attempts')
            found = self._ingest_discovery_result(attempt, market, result, config)
            self.store.db.execute("UPDATE discovery_attempts SET state='completed',result=?,completed_at=? WHERE id=?", (encode({'discoveries': found}), self.clock(), attempt_id))
            self.store.db.execute("UPDATE discovery_markets SET state='observed' WHERE run_id=? AND market_id=?", (self.run_id, market['id']))
            self._discovery_observe()
            self.schedule_discovery(market['id'], 'automatic:' + str(ordinal))
        except (Deferred, ValueError, KeyError, TypeError):
            effects = self.store.all('SELECT state FROM effects WHERE run_id=? AND job_id=?', (self.run_id, attempt_id))
            state = 'unknown' if any(e['state'] in {'running', 'unknown'} for e in effects) else 'failed'
            self.store.db.execute('UPDATE discovery_attempts SET state=? WHERE id=?', (state, attempt_id))
            self.store.db.execute('UPDATE discovery_markets SET state=?,next_due_at=NULL WHERE run_id=? AND market_id=?', (state, self.run_id, market['id']))
            raise

    def _ingest_discovery_result(self, attempt, market, result, config):
        if not isinstance(result, dict) or result.get('status') != 200:
            raise ValueError('discovery source did not return a usable page')
        content = text(result.get('text'), 'source content', 12000)
        url = source_url(result.get('url'), fixture_mode=config['fixture_mode'])
        if config['fixture_mode'] and url.startswith('http:') and url != attempt['source_url']:
            raise ValueError('fixture source does not match configured literal URL')
        digest = result.get('source_id')
        refs = result.get('_source_references', [])
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) or not isinstance(refs, list):
            raise ValueError('missing canonical gateway source evidence')
        reference = next((ref for ref in refs if isinstance(ref, dict) and ref.get('url') == result.get('url')
                          and ref.get('source_id') == digest and ref.get('sha256') == digest[7:]), None)
        if not reference or not isinstance(reference.get('retrieved_at'), str):
            raise ValueError('missing gateway retrieval time and source hash')
        stamp = datetime.fromisoformat(reference['retrieved_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('source retrieval time requires an explicit timezone')
        retrieved = stamp.timestamp()
        if not math.isfinite(retrieved) or retrieved > self.clock() + 5 or retrieved + market['freshness_seconds'] < self.clock():
            raise ValueError('source evidence is stale or has an invalid observation time')
        email = market['lead_email']
        addresses = {address.casefold() for address in re.findall(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', content)}
        if market['kind'] == 'lead' and (not email or email.casefold() not in addresses):
            return []
        service = self.services.get(market['service'])
        available = bool(service and service.capability in self.policy.capabilities)
        estimate = None
        if available:
            try:
                plan = service.plan(market['scope'], self.policy.max_job_micro_usd)
                available = self.tools.available(plan.action)
                estimate = integer(plan.maximum_micro_usd, 'service maximum', 0, self.policy.max_job_micro_usd)
            except (ValueError, KeyError, TypeError):
                available = False
        found = []
        for term in market['need_terms']:
            match = re.search(re.escape(term), content, re.IGNORECASE)
            if not match:
                continue
            excerpt = content[max(0, match.start() - 160):min(len(content), match.end() + 300)]
            evidence = {'url': url, 'configured_source_url': attempt['source_url'], 'title': str(result.get('title', ''))[:500],
                        'excerpt': excerpt, 'matched_term': term, 'source_id': digest, 'retrieved_at': retrieved,
                        'trust': 'untrusted source text; relevance and demand are hypotheses', 'fixture': config['fixture_mode']}
            key = identity(self.run_id, url, market['service'], term.casefold(), market['kind'], email)
            opportunity = identity(self.run_id, 'discovery', key)
            existing = self.store.one('SELECT id FROM opportunities WHERE id=? AND run_id=?', (opportunity, self.run_id))
            if not existing:
                self.receive(source='discovery', external_id=key, service=market['service'], scope=market['scope'],
                    customer_email=email, origin='discovered', source_reference=url, priority=market['priority'])
            uncertainty = ['A matching source statement does not establish willingness to pay.',
                           'Retrieval freshness does not establish the publication date.',
                           'Prices are configured proposals; no customer promise or payment is counted.']
            next_action = 'Operator may authorize one bounded prototype experiment.' if market['kind'] == 'experiment' else 'Assess the identified lead under existing outreach permissions and opt-outs.'
            with self.store.transaction():
                self.store.db.execute('INSERT OR IGNORE INTO discoveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (key, self.run_id, market['id'], opportunity, market['kind'], excerpt, market['proposed_offering'], encode(evidence),
                     encode({'configured': list(self.policy.capabilities), 'service_available': available}), estimate,
                     encode(uncertainty), next_action, self.clock(), retrieved, retrieved + market['freshness_seconds']))
                self.store.db.execute('UPDATE discoveries SET evidence=?,last_seen_at=?,expires_at=? WHERE id=? AND last_seen_at<=?',
                    (encode(evidence), retrieved, retrieved + market['freshness_seconds'], key, retrieved))
                self.store.db.execute('INSERT OR IGNORE INTO discovery_observations VALUES (?,?,?,?,?,?)',
                    (identity(key, attempt['id']), self.run_id, key, attempt['id'], encode(evidence), retrieved))
                latest = self.store.one('SELECT status,assessment FROM opportunities WHERE id=?', (opportunity,))
                missing_evidence = latest['status'] == 'needs_review' and json.loads(latest['assessment']).get('reason') == 'canonical discovery evidence is missing or stale'
                if market['kind'] == 'experiment':
                    if latest['status'] == 'received' or missing_evidence:
                        self.store.db.execute("UPDATE opportunities SET status='experiment_proposed' WHERE id=? AND customer_id IS NULL", (opportunity,))
                else:
                    if missing_evidence:
                        self.store.db.execute("UPDATE opportunities SET status='received' WHERE id=? AND NOT EXISTS(SELECT 1 FROM quotes WHERE opportunity_id=?)", (opportunity, opportunity))
                    self._enqueue('select', opportunity, 'canonical-evidence:' + attempt['id'])
            found.append(opportunity)
        return found

    def start_experiment(self, opportunity_id, spec, request_id):
        from .engine import Deferred
        single_line(request_id, 'experiment request id', 200)
        fingerprint = identity(opportunity_id, spec)
        previous = self.store.one('SELECT * FROM discovery_experiments WHERE run_id=? AND request_id=?', (self.run_id, request_id))
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('experiment request reused with changed contents')
            return previous['id']
        spec = experiment_spec(spec, self.clock())
        config = self._discovery_config()
        if not config['enabled'] or not self._active():
            raise ValueError('runtime/discovery must be enabled before starting an experiment')
        discovery = self.store.one('SELECT * FROM discoveries WHERE opportunity_id=? AND run_id=?', (opportunity_id, self.run_id))
        opportunity = self.store.one('SELECT * FROM opportunities WHERE id=? AND run_id=?', (opportunity_id, self.run_id))
        if not discovery or discovery['kind'] != 'experiment' or discovery['expires_at'] <= self.clock() or not opportunity or opportunity['customer_id'] is not None:
            raise ValueError('a fresh self-initiated discovery without a fabricated customer is required')
        service = self.services.get(opportunity['service'])
        if not service or service.capability not in self.policy.capabilities:
            raise ValueError('experiment capability is disabled')
        plan = service.plan(json.loads(opportunity['scope']), spec['allowance_micro_usd'])
        integer(plan.maximum_micro_usd, 'experiment service maximum', 0, spec['allowance_micro_usd'])
        if not self.tools.available(plan.action):
            raise ValueError('experiment tool is unavailable')
        experiment_id = identity(self.run_id, opportunity_id, 'bounded-experiment')
        with self.store.transaction():
            if self.store.one('SELECT id FROM discovery_experiments WHERE opportunity_id=?', (opportunity_id,)):
                raise ValueError('this opportunity already has its single bounded experiment')
            self._discovery_allocate(experiment_id, spec['allowance_micro_usd'])
            self.store.db.execute("INSERT INTO discovery_experiments(id,run_id,opportunity_id,request_id,fingerprint,hypothesis,allowance_micro_usd,deadline,success_criteria,stop_conditions,plan,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'awaiting_start',?)",
                (experiment_id, self.run_id, opportunity_id, request_id, fingerprint, spec['hypothesis'], spec['allowance_micro_usd'],
                 spec['deadline'], encode(spec['success_criteria']), encode(spec['stop_conditions']), encode(asdict(plan)), self.clock()))
            self.notify(source='experiment', external_id=experiment_id + ':create', kind='experiment.step', payload={'id': experiment_id})
            self.notify(source='experiment', external_id=experiment_id + ':deadline', kind='experiment.deadline', payload={'id': experiment_id}, due_at=spec['deadline'])
        return experiment_id

    def stop_experiment(self, experiment_id, reason):
        reason = text(reason, 'experiment stop reason', 1000)
        with self.store.transaction():
            experiment = self.store.one('SELECT * FROM discovery_experiments WHERE id=? AND run_id=?', (experiment_id, self.run_id))
            if not experiment:
                raise ValueError('unknown experiment')
            self.store.db.execute("UPDATE discovery_experiments SET state='stopped',completed_at=?,result=? WHERE id=?",
                (self.clock(), encode({'reason': reason, 'commercial_success': False, 'pending_effects_require_reconciliation': True}), experiment_id))
            self.store.memory(self.run_id, experiment_id, 'experiment_stop:' + identity(reason), {'reason': reason}, self.clock())
        return {'stopped': True, 'experiment_id': experiment_id}

    def link_experiment_order(self, experiment_id, order_id, evidence_reference):
        reference = text(evidence_reference, 'order attribution evidence', 1000)
        with self.store.transaction():
            experiment = self.store.one('SELECT e.*,o.service FROM discovery_experiments e JOIN opportunities o ON o.id=e.opportunity_id WHERE e.id=? AND e.run_id=?', (experiment_id, self.run_id))
            order = self.store.one('SELECT * FROM orders WHERE id=? AND run_id=?', (order_id, self.run_id))
            if not experiment or not order or order['service'] != experiment['service']:
                raise ValueError('experiment attribution requires an existing same-run matching-service order')
            existing = self.store.one('SELECT * FROM discovery_experiment_orders WHERE order_id=?', (order_id,))
            if existing and (existing['experiment_id'], existing['evidence_reference']) != (experiment_id, reference):
                raise ValueError('order attribution is immutable and unique')
            self.store.db.execute('INSERT OR IGNORE INTO discovery_experiment_orders VALUES (?,?,?,?,?)', (self.run_id, experiment_id, order_id, reference, self.clock()))
            self.notify(source='experiment-attribution', external_id=identity(experiment_id, order_id), kind='discovery.observe', payload={})
        return {'linked': True, 'experiment_id': experiment_id, 'order_id': order_id}

    def experiment_artifact(self, experiment_id):
        row = self.store.one('SELECT artifact FROM discovery_experiments WHERE id=? AND run_id=?', (experiment_id, self.run_id))
        if not row or not row['artifact']:
            raise ValueError('no completed prototype artifact is available')
        artifact = json.loads(row['artifact'])
        self._validate_artifact(artifact)
        return artifact

    def _experiment_step(self, experiment_id):
        from .engine import Deferred
        experiment = self.store.one('SELECT * FROM discovery_experiments WHERE id=? AND run_id=?', (experiment_id, self.run_id))
        if not experiment or experiment['state'] in {'completed', 'failed', 'expired', 'stopped'}:
            return
        if experiment['deadline'] <= self.clock():
            self._experiment_deadline(experiment_id)
            return
        opportunity = self.store.one('SELECT * FROM opportunities WHERE id=?', (experiment['opportunity_id'],))
        service = self.services.get(opportunity['service'])
        if not self._discovery_config()['enabled'] or not service or service.capability not in self.policy.capabilities:
            raise Deferred('experiment configuration or capability disabled')
        plan = json.loads(experiment['plan'])
        if experiment['allowance_micro_usd'] > self.policy.max_job_micro_usd:
            raise Deferred('experiment allowance exceeds current per-job policy')
        self.store.db.execute("UPDATE discovery_experiments SET state='running' WHERE id=? AND state IN ('awaiting_start','running','unknown')", (experiment_id,))
        try:
            result = self._authorized_discovery_tool(experiment_id, 'experiment-create', plan['action'], plan['arguments'],
                plan['maximum_micro_usd'], 'discovery_experiments')
            artifact = service.deliverable(json.loads(opportunity['scope']), result)
            self._validate_artifact(artifact)
            # This is a private prototype, not a paid customer deliverable or sale.
            self.store.db.execute("UPDATE discovery_experiments SET state='completed',artifact=?,completed_at=?,result=? WHERE id=? AND state='running'",
                (encode(artifact), self.clock(), encode({'prototype_valid': True, 'commercial_success': False,
                'lesson': 'One prototype was validated. Customer demand and contribution require separate verified outcomes.'}), experiment_id))
            self._discovery_observe()
        except (Deferred, ValueError, KeyError, TypeError):
            effects = self.store.all('SELECT state FROM effects WHERE run_id=? AND job_id=?', (self.run_id, experiment_id))
            state = 'unknown' if any(e['state'] in {'running', 'unknown'} for e in effects) else 'failed'
            self.store.db.execute('UPDATE discovery_experiments SET state=?,result=? WHERE id=? AND state=\'running\'',
                (state, encode({'prototype_valid': False, 'commercial_success': False, 'reason': 'Resolve the original effect; never automatically repeat the experiment.'}), experiment_id))
            raise

    def _experiment_deadline(self, experiment_id):
        experiment = self.store.one('SELECT * FROM discovery_experiments WHERE id=? AND run_id=?', (experiment_id, self.run_id))
        if not experiment or experiment['deadline'] > self.clock():
            return
        if experiment['state'] not in {'completed', 'failed', 'expired', 'stopped'}:
            self.store.db.execute("UPDATE discovery_experiments SET state='expired',completed_at=?,result=? WHERE id=?",
                (self.clock(), encode({'reason': 'deadline reached; no further execution is authorized', 'commercial_success': False,
                'unresolved_effects_preserved': True}), experiment_id))
        self._discovery_observe()

    def _record_discovery_outcome(self, market_id, entity, kind, document):
        row_id = identity(self.run_id, entity, kind)
        self.store.db.execute('INSERT INTO discovery_outcomes VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document,observed_at=excluded.observed_at',
            (row_id, self.run_id, market_id, entity, kind, encode(document), self.clock()))
        self.store.memory(self.run_id, entity, 'discovery_outcome:' + identity(document), document, self.clock())

    def _schedule_discovery_observation(self):
        config = self._discovery_config()
        if not config['enabled']:
            return
        interval = min((market['interval_seconds'] for market in config['markets']), default=86400)
        bucket = int(self.clock() // interval) + 1
        self.notify(source='discovery-outcomes', external_id=str(bucket) + ':' + identity(config), kind='discovery.observe',
                    payload={}, due_at=bucket * interval)

    def _work_cost_observation(self, reservation):
        if not reservation:
            return {'actual_micro_usd': None, 'costs_final': False, 'state': 'not_observed'}
        try:
            result = self.economy.work_cost(run_id=self.run_id, reservation_id=reservation)
            actual = result.get('actual_micro_usd')
            if actual is not None:
                integer(actual, 'observed actual work cost')
            if type(result.get('costs_final')) is not bool:
                raise ValueError('cost finality required')
            return {'actual_micro_usd': actual, 'costs_final': result['costs_final'] and actual is not None, 'state': result.get('state', 'unknown')}
        except Exception:
            return {'actual_micro_usd': None, 'costs_final': False, 'state': 'unavailable'}

    def _observation_batch(self, kind, rows, limit=5):
        """Durable fair cursor bounds remote reads without dropping old history."""
        if not rows:
            return []
        saved = self.store.one('SELECT cursor FROM discovery_observation_cursors WHERE run_id=? AND kind=?', (self.run_id, kind))
        cursor = saved['cursor'] if saved else ''
        rows = sorted(rows, key=lambda row: row['id'])
        ordered = [row for row in rows if row['id'] > cursor] + [row for row in rows if row['id'] <= cursor]
        selected = ordered[:limit]
        self.store.db.execute('INSERT INTO discovery_observation_cursors VALUES (?,?,?) ON CONFLICT(run_id,kind) DO UPDATE SET cursor=excluded.cursor', (self.run_id, kind, selected[-1]['id']))
        return selected

    def _discovery_observe(self):
        """Read local authorities and durable conversations; never execute a tool."""
        work = self.store.all("SELECT id,market_id,reservation_id,'research_cost' AS kind FROM discovery_attempts WHERE run_id=? UNION SELECT e.id,d.market_id,e.reservation_id,'prototype_cost' AS kind FROM discovery_experiments e JOIN discoveries d ON d.opportunity_id=e.opportunity_id WHERE e.run_id=?", (self.run_id, self.run_id))
        pending = []
        for item in work:
            prior = self.store.one('SELECT document FROM discovery_outcomes WHERE run_id=? AND entity_id=? AND kind=?', (self.run_id, item['id'], item['kind']))
            document = json.loads(prior['document']) if prior else {}
            # Settled reservations/charges are immutable in the economy contract.
            # Unknown subtotals retain their cursor position until final evidence.
            if not document.get('costs_final'):
                pending.append(item)
        for item in self._observation_batch('work-costs', pending):
            self._record_discovery_outcome(item['market_id'], item['id'], item['kind'], self._work_cost_observation(item['reservation_id']))
        for opportunity in self.store.all('SELECT o.*,d.market_id FROM opportunities o JOIN discoveries d ON d.opportunity_id=o.id WHERE o.run_id=?', (self.run_id,)):
            responses = self.store.one("SELECT COUNT(*) AS n FROM conversations WHERE run_id=? AND customer_id=? AND direction='inbound' AND created_at>=?", (self.run_id, opportunity['customer_id'], opportunity['created_at']))['n'] if opportunity['customer_id'] else 0
            revisions = self.store.one("SELECT COUNT(*) AS n FROM memory WHERE run_id=? AND entity_id=? AND kind LIKE 'revision:%'", (self.run_id, opportunity['id']))['n']
            self._record_discovery_outcome(opportunity['market_id'], opportunity['id'], 'opportunity',
                {'status': opportunity['status'], 'responses': responses, 'revisions': revisions,
                 'conversion': False, 'lesson': 'Responses are observed messages, not payment, delivery or positive financial performance.'})
        orders = self.store.all('''SELECT DISTINCT o.*,d.market_id FROM orders o JOIN quotes q ON q.id=o.quote_id
            JOIN discoveries d ON d.opportunity_id=q.opportunity_id WHERE o.run_id=?
            UNION SELECT DISTINCT o.*,d.market_id FROM orders o JOIN discovery_experiment_orders l ON l.order_id=o.id
            JOIN discovery_experiments e ON e.id=l.experiment_id JOIN discoveries d ON d.opportunity_id=e.opportunity_id WHERE o.run_id=?''', (self.run_id, self.run_id))
        for order in self._observation_batch('orders', orders):
            try:
                snapshot = self.economy.payment(run_id=self.run_id, job_id=order['id'])
                if snapshot.get('run_id') != self.run_id or snapshot.get('job_id') != order['id'] or snapshot.get('mode') != self.mode or snapshot.get('currency') != 'USD':
                    raise ValueError('financial observation identity mismatch')
                for key in ('revision', 'receipts_micro_usd', 'refunds_micro_usd', 'fees_micro_usd', 'earned_micro_usd', 'operating_cost_micro_usd'):
                    integer(snapshot.get(key), key)
                if type(snapshot.get('costs_final')) is not bool:
                    raise ValueError('financial finality required')
                old = self.store.one("SELECT document FROM discovery_outcomes WHERE run_id=? AND entity_id=? AND kind='order'", (self.run_id, order['id']))
                old = json.loads(old['document']) if old else None
                if old and old.get('snapshot', {}).get('revision', -1) > snapshot['revision']:
                    raise ValueError('stale financial observation')
                realized = order['status'] == 'completed' and snapshot['costs_final'] and snapshot['status'] in {'settled', 'partial', 'partially_refunded', 'refunded'}
                earned = min(snapshot['earned_micro_usd'], max(0, snapshot['receipts_micro_usd'] - snapshot['refunds_micro_usd']))
                contribution = earned - snapshot['fees_micro_usd'] - snapshot['operating_cost_micro_usd'] if realized else None
                responses = self.store.one("SELECT COUNT(*) AS n FROM conversations WHERE run_id=? AND customer_id=? AND direction='inbound' AND created_at>=?", (self.run_id, order['customer_id'], order['created_at']))['n']
                revisions = self.store.one("SELECT COUNT(*) AS n FROM memory WHERE run_id=? AND entity_id IN (?,(SELECT opportunity_id FROM quotes WHERE id=?)) AND kind LIKE 'revision:%'", (self.run_id, order['id'], order['quote_id']))['n']
                document = {'snapshot': snapshot, 'order_status': order['status'], 'created_at': order['created_at'], 'completed_at': order['completed_at'], 'responses': responses, 'revisions': revisions,
                            'conversion': bool(realized and earned > 0 and snapshot['status'] not in {'refunded'}),
                            'measured_contribution_micro_usd': contribution, 'costs_final': snapshot['costs_final'],
                            'refunds_micro_usd': snapshot['refunds_micro_usd'], 'delivery_cost_micro_usd': snapshot['operating_cost_micro_usd']}
                self._record_discovery_outcome(order['market_id'], order['id'], 'order', document)
            except Exception:
                prior = self.store.one("SELECT document FROM discovery_outcomes WHERE run_id=? AND entity_id=? AND kind='order'", (self.run_id, order['id']))
                prior = json.loads(prior['document']) if prior else {}
                self._record_discovery_outcome(order['market_id'], order['id'], 'order', {'snapshot': prior.get('snapshot', {}), 'measured_contribution_micro_usd': None, 'costs_final': False, 'reason': 'authoritative financial observation unavailable; no positive performance credit'})
        for experiment in self.store.all('SELECT e.id,e.reservation_id,e.state,e.created_at,e.deadline,e.success_criteria,e.result,d.market_id FROM discovery_experiments e JOIN discoveries d ON d.opportunity_id=e.opportunity_id WHERE e.run_id=?', (self.run_id,)):
            linked = [r['order_id'] for r in self.store.all('SELECT order_id FROM discovery_experiment_orders WHERE experiment_id=? AND run_id=?', (experiment['id'], self.run_id))]
            documents = []
            for order in linked:
                row = self.store.one("SELECT document FROM discovery_outcomes WHERE entity_id=? AND run_id=? AND kind='order'", (order, self.run_id))
                document = json.loads(row['document']) if row else {}
                current = self.store.one('SELECT status,payment FROM orders WHERE id=? AND run_id=?', (order, self.run_id))
                cached_payment = json.loads(current['payment']) if current else {}
                if not current or current['status'] != document.get('order_status') or cached_payment.get('revision', -1) > document.get('snapshot', {}).get('revision', -1) or self.store.one("SELECT 1 FROM effects WHERE run_id=? AND job_id=? AND state IN ('running','unknown','planned') LIMIT 1", (self.run_id, order)):
                    document.update(costs_final=False, measured_contribution_micro_usd=None)
                documents.append(document)
            saved_cost = self.store.one("SELECT document FROM discovery_outcomes WHERE run_id=? AND entity_id=? AND kind='prototype_cost'", (self.run_id, experiment['id']))
            cost = json.loads(saved_cost['document']) if saved_cost else {'costs_final': False, 'actual_micro_usd': None}
            research = [json.loads(r['document']) for r in self.store.all("SELECT document FROM discovery_outcomes WHERE run_id=? AND market_id=? AND kind='research_cost'", (self.run_id, experiment['market_id']))]
            known = cost['costs_final'] and all(r.get('costs_final') and type(r.get('actual_micro_usd')) is int for r in research) and all(d.get('costs_final') and type(d.get('measured_contribution_micro_usd')) is int for d in documents)
            expected_research = self.store.one('SELECT COUNT(*) AS n FROM discovery_attempts WHERE run_id=? AND market_id=?', (self.run_id, experiment['market_id']))['n']
            unresolved = self.store.one("SELECT COUNT(*) AS n FROM effects WHERE run_id=? AND state IN ('running','unknown','planned') AND (job_id=? OR job_id IN (SELECT id FROM discovery_attempts WHERE run_id=? AND market_id=?))", (self.run_id, experiment['id'], self.run_id, experiment['market_id']))['n']
            known = known and len(research) == expected_research and not unresolved
            in_window = [d for d in documents if type(d.get('created_at')) in {int, float}
                         and d['created_at'] >= experiment['created_at'] and type(d.get('completed_at')) in {int, float}
                         and experiment['created_at'] <= d['completed_at'] <= experiment['deadline']]
            conversions = sum(d.get('conversion') is True for d in in_window)
            acquisition = sum(r['actual_micro_usd'] for r in research) if known else None
            contribution = sum(d['measured_contribution_micro_usd'] for d in in_window) - cost['actual_micro_usd'] - acquisition if known else None
            lifetime = sum(d['measured_contribution_micro_usd'] for d in documents) - cost['actual_micro_usd'] - acquisition if known else None
            criteria = json.loads(experiment['success_criteria'])
            previous = json.loads(experiment['result'])
            previous.update({'conversions': conversions, 'measured_contribution_micro_usd': contribution,
                'acquisition_cost_micro_usd': acquisition, 'cost_boundary': 'All recorded research for the source market, this prototype, and linked order fulfillment costs; hosting remains unallocated.',
                'evaluation_deadline': experiment['deadline'], 'late_or_prior_conversions': sum(d.get('conversion') is True for d in documents) - conversions,
                'lifetime_contribution_micro_usd': lifetime,
                'costs_final': known, 'commercial_success': bool(experiment['state'] == 'completed' and known and conversions > 0
                    and conversions >= criteria['min_conversions'] and contribution > 0 and contribution >= criteria['min_contribution_micro_usd'])})
            self.store.db.execute('UPDATE discovery_experiments SET result=? WHERE id=? AND run_id=?', (encode(previous), experiment['id'], self.run_id))
        self._schedule_discovery_observation()

    def _discovery_priorities(self):
        config = self._discovery_config()
        rows = self.store.all('SELECT market_id,entity_id,kind,document FROM discovery_outcomes WHERE run_id=?', (self.run_id,))
        result = []
        for market in config['markets']:
            observed = [(r['kind'], json.loads(r['document'])) for r in rows if r['market_id'] == market['id']]
            costs = [d for kind, d in observed if kind in {'research_cost', 'prototype_cost'}]
            orders = [d for kind, d in observed if kind == 'order']
            known = all(c.get('costs_final') is True and type(c.get('actual_micro_usd')) is int for c in costs)
            expected_costs = self.store.one('SELECT (SELECT COUNT(*) FROM discovery_attempts WHERE run_id=? AND market_id=?) + (SELECT COUNT(*) FROM discovery_experiments e JOIN discoveries d ON d.opportunity_id=e.opportunity_id WHERE e.run_id=? AND d.market_id=?) AS n', (self.run_id, market['id'], self.run_id, market['id']))['n']
            unresolved = self.store.one("SELECT COUNT(*) AS n FROM effects WHERE run_id=? AND state IN ('running','unknown','planned') AND (job_id IN (SELECT id FROM discovery_attempts WHERE run_id=? AND market_id=?) OR job_id IN (SELECT e.id FROM discovery_experiments e JOIN discoveries d ON d.opportunity_id=e.opportunity_id WHERE e.run_id=? AND d.market_id=?))", (self.run_id, self.run_id, market['id'], self.run_id, market['id']))['n']
            known = known and len(costs) == expected_costs and not unresolved
            known = known and all(o.get('costs_final') is True and type(o.get('measured_contribution_micro_usd')) is int for o in orders)
            actual_orders = self.store.all('''SELECT DISTINCT o.id,o.status,o.payment FROM orders o JOIN quotes q ON q.id=o.quote_id
                JOIN discoveries d ON d.opportunity_id=q.opportunity_id WHERE o.run_id=? AND d.market_id=?
                UNION SELECT DISTINCT o.id,o.status,o.payment FROM orders o JOIN discovery_experiment_orders l ON l.order_id=o.id
                JOIN discovery_experiments e ON e.id=l.experiment_id JOIN discoveries d ON d.opportunity_id=e.opportunity_id
                WHERE o.run_id=? AND d.market_id=?''', (self.run_id, market['id'], self.run_id, market['id']))
            observations = {r['entity_id']: json.loads(r['document']) for r in rows if r['market_id'] == market['id'] and r['kind'] == 'order'}
            known = known and {o['id'] for o in actual_orders} == set(observations)
            for order in actual_orders:
                observation = observations.get(order['id'], {})
                payment = json.loads(order['payment'])
                if observation.get('order_status') != order['status'] or payment.get('revision', -1) > observation.get('snapshot', {}).get('revision', -1):
                    known = False
                if self.store.one("SELECT 1 FROM effects WHERE run_id=? AND job_id=? AND state IN ('running','unknown','planned') LIMIT 1", (self.run_id, order['id'])):
                    known = False
            contribution = sum(o['measured_contribution_micro_usd'] for o in orders) - sum(c['actual_micro_usd'] for c in costs) if known else None
            conversions = sum(o.get('conversion') is True for o in orders)
            delta = 0
            if known and contribution is not None and len(orders) >= config['adaptation_min_samples']:
                units = abs(contribution) // 100000
                delta = min(config['adaptation_limit'], units) * (1 if contribution >= 0 and conversions else -1)
            result.append({'market_id': market['id'], 'base_priority': market['priority'], 'adaptation_delta': delta,
                           'priority': max(0, min(100, market['priority'] + delta)), 'verified_samples': len(orders) if known else 0,
                           'conversions': conversions, 'costs_final': known, 'measured_contribution_micro_usd': contribution})
        return result

    def _discovery_priority(self, opportunity):
        discovery = self.store.one('SELECT market_id FROM discoveries WHERE opportunity_id=? AND run_id=?', (opportunity['id'], self.run_id))
        if discovery:
            result = next((m for m in self._discovery_priorities() if m['market_id'] == discovery['market_id']), None)
            if result:
                return result['priority']
        return opportunity['priority']

    def discovery_status(self, offset=0, limit=100):
        integer(offset, 'history offset', 0, 1000000)
        integer(limit, 'history limit', 1, 100)
        tables = {'discoveries': 'discoveries', 'attempts': 'discovery_attempts', 'experiments': 'discovery_experiments',
                  'outcomes': 'discovery_outcomes', 'observations': 'discovery_observations'}
        result = {'run_id': self.run_id, 'mode': self.mode, 'runtime_state': self.runtime.state(self.run_id),
                  'config': self._discovery_config(), 'history_offset': offset, 'history_limit': limit, 'totals': {},
                  'priorities': self._discovery_priorities()}
        for name, table in tables.items():
            result['totals'][name] = self.store.one('SELECT COUNT(*) AS n FROM ' + table + ' WHERE run_id=?', (self.run_id,))['n']
            rows = self.store.all('SELECT * FROM ' + table + ' WHERE run_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?', (self.run_id, limit, offset))
            for row in rows:
                for key in {'evidence', 'capabilities', 'uncertainty', 'result', 'market_document', 'success_criteria', 'stop_conditions', 'plan', 'artifact', 'document'}:
                    if row.get(key) is not None and isinstance(row.get(key), str):
                        row[key] = json.loads(row[key])
                if table == 'discovery_experiments':
                    if isinstance(row.get('plan'), dict):
                        arguments = row['plan'].pop('arguments', {})
                        row['plan']['arguments_sha256'] = hashlib.sha256(encode(arguments).encode()).hexdigest()
                    if isinstance(row.get('artifact'), dict):
                        content = row['artifact'].pop('content', '')
                        row['artifact']['content_sha256'] = hashlib.sha256(content.encode()).hexdigest()
                        row['artifact']['content_bytes'] = len(content.encode())
                    row['linked_order_ids'] = [r['order_id'] for r in self.store.all('SELECT order_id FROM discovery_experiment_orders WHERE experiment_id=? AND run_id=?', (row['id'], self.run_id))]
                    row['overdue'] = row['deadline'] <= self.clock() and row['state'] not in {'completed', 'failed', 'expired', 'stopped'}
                elif table == 'discoveries':
                    experiment = self.store.one('SELECT id FROM discovery_experiments WHERE opportunity_id=? AND run_id=?', (row['opportunity_id'], self.run_id))
                    row['experiment_id'] = experiment['id'] if experiment else None
                elif table == 'discovery_attempts' and isinstance(row.get('market_document'), dict):
                    market = row['market_document']
                    row['market_document'] = {key: market[key] for key in ('id', 'label', 'service', 'kind')}
                    row['market_document']['scope_sha256'] = hashlib.sha256(encode(market['scope']).encode()).hexdigest()
            result[name] = rows
        result['totals'].update({'allocated_micro_usd': self._discovery_allocated(), 'allowance_micro_usd': result['config']['allowance_micro_usd']})
        result['markets'] = self.store.all('SELECT * FROM discovery_markets WHERE run_id=? ORDER BY market_id', (self.run_id,))
        upcoming_filter = "run_id=? AND kind IN ('discovery.run','experiment.step','experiment.deadline','discovery.observe') AND state IN ('queued','running','blocked')"
        result['totals']['upcoming'] = self.store.one('SELECT COUNT(*) AS n FROM events WHERE ' + upcoming_filter, (self.run_id,))['n']
        result['upcoming'] = self.store.all('SELECT id,kind,due_at,state,error FROM events WHERE ' + upcoming_filter + ' ORDER BY due_at,id LIMIT ? OFFSET ?', (self.run_id, limit, offset))
        effects_filter = 'run_id=? AND (job_id IN (SELECT id FROM discovery_attempts WHERE run_id=?) OR job_id IN (SELECT id FROM discovery_experiments WHERE run_id=?))'
        result['totals']['effects'] = self.store.one('SELECT COUNT(*) AS n FROM effects WHERE ' + effects_filter, (self.run_id, self.run_id, self.run_id))['n']
        result['effects'] = self.store.all('SELECT id,job_id,provider,action,state,reason,attempt,created_at FROM effects WHERE ' + effects_filter + ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (self.run_id, self.run_id, self.run_id, limit, offset))
        return result
