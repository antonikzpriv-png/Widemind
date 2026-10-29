"""Optional free Gmail inbox scheduling through the existing durable worker."""
import math


def poll_seconds(env):
    value=env.get('WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS','0')
    if value is None or isinstance(value,str) and not value.strip():return 0
    if not isinstance(value,str) or not value.strip().isascii() or not value.strip().isdecimal():
        raise ValueError('WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS must be 0 or an integer from 60 to 86400')
    seconds=int(value.strip())
    if seconds!=0 and not 60<=seconds<=86400:
        raise ValueError('WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS must be 0 or an integer from 60 to 86400')
    return seconds


def _pending(business,seconds,now):
    store,run=business.store,business.run_id
    if store.one("SELECT id FROM events WHERE run_id=? AND kind='gmail.sync' AND state IN ('queued','running','blocked') LIMIT 1",(run,)):
        return True
    if store.one("SELECT id FROM effects WHERE run_id=? AND state IN ('running','unknown','planned') AND (action='gmail.read' OR (provider='economy' AND action='reserve_conversation' AND job_id='inbox')) LIMIT 1",(run,)):
        return True
    latest=store.one("SELECT due_at FROM events WHERE run_id=? AND source='conversation-inbox-poll' ORDER BY due_at DESC LIMIT 1",(run,))
    return bool(latest and now-latest['due_at']<seconds)


def schedule_inbox(business,env):
    """Queue at most one zero-cost page; never read mail or invoke a model here.

    Blocked/unknown reads require observational recovery or operator action, not
    a fresh action ID on every clock bucket. The event journal itself is the
    durable cadence record, so process restart does not reset the poll bound.
    """
    seconds=poll_seconds(env)
    if not seconds or not business.policy.conversation_enabled or not business._active():return
    now=business.clock()
    if type(now) not in (int,float) or not math.isfinite(now) or now<0:
        raise ValueError('invalid conversation scheduling clock')
    if _pending(business,seconds,now):return
    # Capability I/O is free and outside the write transaction. No Gmail/model
    # request executes in this scheduling operation.
    if not business.tools.available('gmail.read') or not business._active():return
    with business.store.transaction():
        if _pending(business,seconds,now):return
        business.notify(source='conversation-inbox-poll',external_id=f'{seconds}:{int(now//seconds)}',
                        kind='gmail.sync',payload={'cursor':None},due_at=now)
