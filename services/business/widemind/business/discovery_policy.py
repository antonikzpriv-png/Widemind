"""Strict owner configuration for bounded, evidence-led discovery.

Source text is never configuration. Markets select literal terms and registered
services; interpretation remains an explicitly uncertain business hypothesis.
"""
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .policy import integer
from .services import single_line, text, web_url


def source_url(value, *, fixture_mode=False):
    if not fixture_mode:
        web_url(value)
    else:
        single_line(value, 'source URL', 2048)
        parsed = urlsplit(value)
        if parsed.scheme == 'http':
            try:
                local = ipaddress.ip_address(parsed.hostname or '').is_loopback
            except ValueError:
                local = False
            if not local or parsed.username or parsed.password:
                raise ValueError('fixture source must be literal HTTP loopback')
        else:
            web_url(value)
    parsed = urlsplit(value)
    if any(c.isspace() for c in value) or '\\' in value:
        raise ValueError('invalid source URL')
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    if port and port != (443 if parsed.scheme == 'https' else 80):
        host += ':' + str(port)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((parsed.scheme, host, parsed.path or '/', urlencode(sorted(query)), ''))


def string_list(value, name, *, maximum=10, limit=300):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(name + ' must be a bounded nonempty list')
    values = [single_line(v, name, limit) for v in value]
    if len({v.casefold() for v in values}) != len(values):
        raise ValueError(name + ' must be unique')
    return values


def configuration(document, mode):
    if not isinstance(document, dict) or set(document) - {
            'enabled', 'fixture_mode', 'allowance_micro_usd', 'adaptation_limit',
            'adaptation_min_samples', 'markets'}:
        raise ValueError('invalid discovery configuration')
    enabled, fixture = document.get('enabled', False), document.get('fixture_mode', False)
    if type(enabled) is not bool or type(fixture) is not bool or fixture and mode != 'test':
        raise ValueError('fixture discovery is available only in explicitly identified test mode')
    markets = document.get('markets', [])
    if not isinstance(markets, list) or len(markets) > 20:
        raise ValueError('configure up to twenty markets')
    result = {'enabled': enabled, 'fixture_mode': fixture,
              'allowance_micro_usd': integer(document.get('allowance_micro_usd', 0), 'discovery allowance'),
              'adaptation_limit': integer(document.get('adaptation_limit', 0), 'adaptation limit', 0, 20),
              'adaptation_min_samples': integer(document.get('adaptation_min_samples', 3), 'minimum samples', 1, 100),
              'markets': []}
    seen = set()
    for raw in markets:
        if not isinstance(raw, dict) or set(raw) - {
                'id', 'label', 'need_terms', 'source_urls', 'service', 'scope', 'kind',
                'lead_email', 'proposed_offering', 'research_allowance_micro_usd',
                'interval_seconds', 'freshness_seconds', 'cooldown_seconds', 'max_attempts', 'priority'}:
            raise ValueError('invalid market')
        key = single_line(raw.get('id'), 'market id', 100)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', key) or key in seen:
            raise ValueError('market IDs must be unique identifiers')
        seen.add(key)
        kind = raw.get('kind', 'experiment')
        if kind not in {'lead', 'experiment'}:
            raise ValueError('invalid market opportunity kind')
        email = raw.get('lead_email')
        if email is not None:
            single_line(email, 'lead email', 254)
            if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', email) or kind != 'lead':
                raise ValueError('lead email requires a lead market')
            email = email.lower()
        scope = raw.get('scope')
        from .store import encode
        if not isinstance(scope, dict) or len(encode(scope)) > 50000:
            raise ValueError('market requires bounded service scope')
        urls = raw.get('source_urls')
        if not isinstance(urls, list) or not 1 <= len(urls) <= 5:
            raise ValueError('market requires one to five sources')
        urls = [source_url(url, fixture_mode=fixture) for url in urls]
        if len(set(urls)) != len(urls):
            raise ValueError('duplicate market sources')
        result['markets'].append({
            'id': key, 'label': single_line(raw.get('label', key), 'market label', 200),
            'need_terms': string_list(raw.get('need_terms'), 'need terms', limit=100),
            'source_urls': urls, 'service': single_line(raw.get('service'), 'service', 100),
            'scope': scope, 'kind': kind, 'lead_email': email,
            'proposed_offering': text(raw.get('proposed_offering'), 'proposed offering', 2000),
            'research_allowance_micro_usd': integer(raw.get('research_allowance_micro_usd', 0), 'research allowance'),
            'interval_seconds': integer(raw.get('interval_seconds', 86400), 'research interval', 300, 2592000),
            'freshness_seconds': integer(raw.get('freshness_seconds', 86400), 'source freshness', 60, 2592000),
            'cooldown_seconds': integer(raw.get('cooldown_seconds', 86400), 'cooldown', 300, 2592000),
            'max_attempts': integer(raw.get('max_attempts', 3), 'maximum attempts', 1, 100),
            'priority': integer(raw.get('priority', 1), 'priority', 0, 100)})
    return result


def experiment_spec(spec, now):
    if not isinstance(spec, dict) or set(spec) != {
            'hypothesis', 'allowance_micro_usd', 'deadline', 'success_criteria', 'stop_conditions'}:
        raise ValueError('experiment requires hypothesis, allowance, deadline, success criteria and stop conditions')
    deadline = spec['deadline']
    if type(deadline) not in {int, float} or not now < deadline <= now + 2592000:
        raise ValueError('experiment deadline must be within the next thirty days')
    criteria = spec['success_criteria']
    if not isinstance(criteria, dict) or set(criteria) - {'artifact_valid', 'min_conversions', 'min_contribution_micro_usd'} or criteria.get('artifact_valid') is not True:
        raise ValueError('success criteria require a validated artifact; optional commercial criteria need measured results')
    stops = string_list(spec['stop_conditions'], 'stop conditions', limit=100)
    if set(stops) != {'deadline', 'allowance_exhausted', 'single_attempt', 'scope_failure', 'operator_stop'}:
        raise ValueError('stop conditions must enable deadline, allowance_exhausted, single_attempt, scope_failure and operator_stop')
    return {'hypothesis': text(spec['hypothesis'], 'hypothesis', 2000),
            'allowance_micro_usd': integer(spec['allowance_micro_usd'], 'experiment allowance'),
            'deadline': deadline,
            'success_criteria': {'artifact_valid': True,
                'min_conversions': integer(criteria.get('min_conversions', 0), 'minimum conversions', 0, 1000),
                'min_contribution_micro_usd': integer(criteria.get('min_contribution_micro_usd', 0), 'minimum contribution')},
            'stop_conditions': stops}
