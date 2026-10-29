"""Bounded business inbox reads over the existing authenticated Gmail adapter.

This module never authenticates a customer from a From header alone. Optional
receiver-header trust is owner-selected and supports conversation triage only;
commercial acceptance still requires the authenticated customer portal.
"""
from email.utils import getaddresses
import json
import re

from .tools.contracts import ToolError, ToolResult, Reconciliation


_ID=re.compile(r'^[A-Za-z0-9_-]{1,256}$')
_MAILBOX=re.compile(r'^[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$')
_RFC_ID=re.compile(r'^<[^<>\s@]{1,120}@[^<>\s@]{1,120}>$')


def _bounded_text(value, maximum):
    """Cap encoded JSON size, including Unicode escaping, not just characters."""
    if not isinstance(value,str):return '',False
    if len(json.dumps(value).encode())<=maximum:return value,False
    low,high=0,len(value)
    while low<high:
        middle=(low+high+1)//2
        if len(json.dumps(value[:middle]).encode())<=maximum:low=middle
        else:high=middle-1
    return value[:low],True


def normalize_message(raw,config):
    """Return private bounded task data and explicit sender-verification evidence."""
    if (not isinstance(raw,dict) or not isinstance(raw.get('id'),str) or not _ID.fullmatch(raw['id'])
            or not isinstance(raw.get('thread_id'),str) or not _ID.fullmatch(raw['thread_id'])):
        raise ToolError('gmail_invalid_response','Invalid Gmail message/thread identity.')
    headers=raw.get('headers',[])
    if not isinstance(headers,list) or len(headers)>1000:
        raise ToolError('gmail_invalid_response','Invalid Gmail headers.')
    indexed={}
    for header in headers:
        if not isinstance(header,dict) or not isinstance(header.get('name'),str) or not isinstance(header.get('value'),str):
            raise ToolError('gmail_invalid_response','Invalid Gmail header field.')
        indexed.setdefault(header['name'].lower(),[]).append(header['value'])
    source=indexed.get('from',[])
    if any(any(ord(c)<32 for c in value) for value in source):source=[]
    addresses=getaddresses(source) if len(source)==1 else []
    sender=addresses[0][1].lower() if len(addresses)==1 else ''
    match=_MAILBOX.fullmatch(sender)
    if not match:sender=''
    domain=match.group(1).lower() if match else None
    auth=indexed.get('authentication-results',[])
    verified=False
    if (config.get('conversation_trusted_authserv_id')=='mx.google.com' and sender and len(auth)==1
            and auth[0].split(';',1)[0].strip().lower()=='mx.google.com'
            and not any(key in indexed for key in ('resent-from','resent-sender'))
            and len(indexed.get('sender',[]))<=1):
        # A DMARC claim outside the configured receiving server's own result is
        # not identity evidence. Header presence in a body is never consulted.
        dmarc=re.search(r'(?:^|;)\s*dmarc=pass\b([^;]*)',auth[0],re.IGNORECASE)
        aligned=re.search(r'\bheader\.from=([A-Za-z0-9.-]+)(?=\s|$)',dmarc.group(1),re.IGNORECASE) if dmarc else None
        verified=bool(aligned and aligned.group(1).lower()==domain)
    labels=raw.get('label_ids',[])
    if not isinstance(labels,list) or 'SENT' in labels or 'DRAFT' in labels:verified=False
    parts=raw.get('body_parts',[])
    plain=[item['text'] for item in parts if isinstance(item,dict) and item.get('mime_type')=='text/plain' and isinstance(item.get('text'),str)] if isinstance(parts,list) else []
    body,truncated=_bounded_text('\n'.join(plain),6000)
    subject_values=indexed.get('subject',[])
    subject,_=_bounded_text(subject_values[0] if len(subject_values)==1 else '',1500)
    subject=' '.join(subject.split())
    if any(ord(c)<32 for c in subject):subject=''
    result={'message_id':raw['id'],'thread_id':raw['thread_id'],'from':sender,
            'subject':subject,'body':body,'body_truncated':truncated,'sender_verified':verified,
            'sender_verification':'owner-trusted-receiver-dmarc' if verified else 'unverified-requires-portal-identity',
            'commercial_acceptance':'authenticated-portal-required'}
    message_ids=indexed.get('message-id',[])
    refs=indexed.get('references',[])
    if len(message_ids)==1 and _RFC_ID.fullmatch(message_ids[0]) and len(refs)<=1 and subject:
        references=re.findall(r'<[^<>]+>',refs[0])[-4:] if refs else []
        if all(_RFC_ID.fullmatch(value) for value in references):
            references=[value for value in references if value!=message_ids[0]]+[message_ids[0]]
            result['rfc_message_id']=message_ids[0]
            result['reply']={'thread_id':raw['thread_id'],'subject':subject if subject.lower().startswith('re:') else 'Re: '+subject,
                             'in_reply_to':message_ids[0],'references':references}
    return result


class ConversationGmailAdapter:
    """A single bounded page; checkpoints IDs before any per-message reads."""
    def __init__(self,base):self.base,self.spec=base,base.spec
    def availability(self,config,secrets):return self.base.availability(config,secrets)
    @staticmethod
    def _listing(arguments,config):
        if not isinstance(arguments,dict) or set(arguments)-{'operation','page_token'} or arguments.get('operation')!='conversation_batch':
            raise ToolError('invalid_input','Expected a bounded conversation inbox read.')
        size=config.get('conversation_batch_size',5)
        if type(size) is not int or not 1<=size<=20:raise ToolError('configuration','conversation_batch_size must be 1–20.')
        output=config.get('max_output_bytes',65536)
        if type(output) is not int or output<12000:raise ToolError('configuration','Conversation inbox needs at least 12000 output bytes.')
        size=min(size,max(1,output//12000))
        result={'operation':'list','query':'in:inbox -from:me','max_results':size,'max_pages':1}
        if arguments.get('page_token') is not None:result['page_token']=arguments['page_token']
        return result
    def quote(self,arguments,config):return self.base.quote(self._listing(arguments,config),config)
    def execute(self,arguments,ctx):
        listing=self._listing(arguments,ctx.config)
        state=ctx.load_state().get('conversation_inbox')
        if state and state.get('listing')!=listing:raise ToolError('conflict','Conversation inbox parameters changed.')
        usage=None
        if not state:
            result=self.base.execute(listing,ctx)
            rows=result.data.get('messages',[])
            if not isinstance(rows,list) or len(rows)>listing['max_results']:
                raise ToolError('gmail_invalid_response','Gmail exceeded the inbox page bound.')
            identifiers=[]
            for row in rows:
                identifier=row.get('id') if isinstance(row,dict) else None
                if not isinstance(identifier,str) or not _ID.fullmatch(identifier):raise ToolError('gmail_invalid_response','Gmail returned an invalid inbox ID.')
                identifiers.append(identifier)
            cursor=result.data.get('next_page_token')
            if cursor is not None and (not isinstance(cursor,str) or len(cursor)>4096):raise ToolError('gmail_invalid_response','Invalid Gmail page cursor.')
            state={'listing':listing,'ids':identifiers,'next_cursor':cursor,'messages':[]}
            ctx.save_state({'conversation_inbox':state})
            usage=result.usage
        for identifier in state['ids'][len(state['messages']):]:
            ctx.check()
            result=self.base.execute({'operation':'read','message_id':identifier},ctx)
            normalized=normalize_message(result.data.get('message'),ctx.config)
            if normalized['message_id']!=identifier:raise ToolError('gmail_invalid_response','Gmail message identity changed.')
            state['messages'].append(normalized)
            ctx.save_state({'conversation_inbox':state})
            usage=result.usage
        if usage is None:
            from .tools.contracts import Usage
            usage=Usage(0,ctx.load_state().get('gmail_usage',{}),details={'provider':'gmail','basis':'cumulative_action_attempts_not_quota_units'})
        return ToolResult({'messages':state['messages'],'next_cursor':state['next_cursor']},usage)
    def reconcile(self,arguments,ctx):
        # Only reads are repeated here, against the exact checkpointed IDs. No
        # mail send, model call or provider mutation is hidden in recovery.
        return Reconciliation('completed',self.execute(arguments,ctx),'Recovered the bounded read-only inbox page.')
