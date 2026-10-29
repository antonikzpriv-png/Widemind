"""Customer-specific, evidence-grounded research with separately billed stages."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from urllib.parse import urlsplit

from .contracts import Quote, ToolSpec, ToolError, ToolResult, Reconciliation, Unavailable, Usage
from .research_evidence import make_plan, evaluate_sources


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)


class ResearchWorkflow:
    spec=ToolSpec('research.collect','Research the agreed customer questions and synthesize retrieved evidence with separately authorized child tools.',version=2)

    def availability(self,config,secrets):
        mode=config.get('source_mode','curated')
        sources=config.get('source_urls',[])
        ready=config.get('enabled') is True and (bool(sources) if mode=='curated' else mode=='search')
        ready=ready and config.get('_dependencies_ready',False)
        return {'available':ready,'reason':'Configure research sources/search and enabled research, AI and files child capabilities; each paid stage requires its own economy reservation.'}

    def validate(self,args,config):
        plan=make_plan(args,config)
        mode=config.get('source_mode','curated')
        urls=config.get('source_urls',[])
        if mode not in {'curated','search'} or not isinstance(urls,list) or len(urls)>30:
            raise Unavailable('Configure curated source URLs or the search source mode.')
        if mode=='curated' and not urls:
            raise Unavailable('Configure source_urls for curated research; excerpts are never fabricated.')
        for url in urls:
            if not isinstance(url,str) or len(url)>4096:raise Unavailable('Configure bounded research source URLs.')
            parsed=urlsplit(url)
            fixture=parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'} and config.get('allow_local_http') is True
            if parsed.scheme!='https' and not fixture or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise Unavailable('Curated sources require HTTPS; loopback HTTP requires explicit owner fixture configuration.')
        corrections=config.get('max_correction_passes',1)
        if type(corrections) is not int or not 0<=corrections<=3:raise Unavailable('max_correction_passes must be from zero to three.')
        queries=config.get('max_search_queries',4)
        if type(queries) is not int or not 1<=queries<=8:raise Unavailable('max_search_queries must be from one to eight.')
        if config.get('fetch_mode','http') not in {'http','browser'}:raise Unavailable('fetch_mode must be http or browser.')
        label=config.get('offline_fixture_label','')
        if not isinstance(label,str) or len(label)>400 or '\n' in label or '\r' in label:raise Unavailable('offline_fixture_label must be a bounded owner annotation.')
        return plan

    def quote(self,args,config):
        self.validate(args,config)
        return Quote(provider='research-orchestration',limits={'separately_authorized_children':True,
                       'max_sources':config.get('max_sources',12),'max_correction_passes':config.get('max_correction_passes',1)})

    @staticmethod
    def _stage(ctx,key,inputs,operation):
        state=ctx.load_state().get('research_v2',{})
        stages=state.get('stages',{})
        fingerprint=hashlib.sha256(encoded(inputs).encode()).hexdigest()
        old=stages.get(key)
        if old and old['input_sha256']!=fingerprint:raise ToolError('conflict','A persisted research stage changed inputs.')
        if old and old.get('status')=='completed':return old['result']
        stages[key]={'status':'running','input_sha256':fingerprint,'inputs':inputs}
        ctx.save_state({'research_v2':{**state,'stages':stages}})
        ctx.check()
        result=operation()
        # Persist the completed result even when cancellation arrived just after
        # the provider/file committed. No repeated paid call is needed on resume.
        state=ctx.load_state().get('research_v2',{});stages=state.get('stages',{})
        stages[key]={**stages[key],'status':'completed','result':result}
        ctx.save_state({'research_v2':{**state,'stages':stages}})
        return result

    @staticmethod
    def _child(ctx,key,tool,arguments):
        if ctx.child_executor is None:
            raise Unavailable('Customer research requires the integrated economy child-execution interface; standalone collection cannot run paid synthesis.')
        return asdict(ctx.child_executor.execute(key,tool,arguments))

    def execute(self,args,ctx):
        from .research_synthesis import synthesis_arguments, validate_synthesis, render_document
        self.validate(args,ctx.config)
        plan=self._stage(ctx,'scope',args,lambda:make_plan(args,ctx.config))
        mode=ctx.config.get('source_mode','curated')
        maximum=ctx.config.get('max_sources',12)
        def discover():
            if mode=='curated':return {'urls':list(dict.fromkeys(ctx.config['source_urls']))[:maximum],'mode':'owner-curated'}
            queries=plan['queries'][:ctx.config.get('max_search_queries',4)]
            urls=[];results=[]
            for i,query in enumerate(queries):
                response=self._child(ctx,'search:'+str(i),'research.search',{'query':query,'max_results':min(maximum,10)})
                results.extend(response['data'].get('results',[]))
                urls.extend(item['url'] for item in response['data'].get('results',[]) if isinstance(item,dict) and isinstance(item.get('url'),str))
            return {'urls':list(dict.fromkeys(urls))[:maximum],'mode':'configured-search','results':results}
        discovery=self._stage(ctx,'discovery',{'plan':plan,'mode':mode},discover)
        fetched=[]
        for index,url in enumerate(discovery['urls']):
            tool='browser.read' if ctx.config.get('fetch_mode','http')=='browser' else 'research.fetch'
            arguments={'url':url,'max_text_chars':ctx.config.get('max_source_chars',12000)}
            raw=self._stage(ctx,'fetch:'+str(index),{'tool':tool,**arguments},
                            lambda i=index,t=tool,a=arguments:self._child(ctx,'fetch:'+str(i),t,a))
            data=raw['data']
            if data.get('status',200)!=200 or not data.get('text'):continue
            references=raw.get('references',[])
            item={**data,'references':references,
                  'retrieved_at':data.get('retrieved_at') or next((r.get('retrieved_at') for r in references if r.get('retrieved_at')),None)}
            # Search dates are retained as weaker, explicitly identified source
            # observations only when the fetched page supplied no date itself.
            if not item.get('published_date'):
                observed=next((r for r in discovery.get('results',[]) if r.get('url')==url and r.get('published_date')),None)
                if observed:item.update(published_date=observed['published_date'],date_basis=observed.get('date_basis','search_unverified'))
            fetched.append(item)
        evidence=self._stage(ctx,'evidence',{'plan':plan,'fetched':fetched},lambda:evaluate_sources(plan,fetched,ctx.config))
        if not evidence['sufficient']:
            ctx.save_state({'research_quality_failure':{'code':'insufficient_evidence','issues':evidence['issues']}})
            raise ToolError('insufficient_evidence','Relevant, dated evidence does not cover the agreed research requirements; no completed synthesis was produced.',usage=Usage())
        previous=None;errors=None;draft=None;validation=None
        for round_ in range(ctx.config.get('max_correction_passes',1)+1):
            inputs={'plan':plan,'evidence':evidence,'errors':errors,'previous':previous}
            def synthesize():
                arguments=synthesis_arguments(plan,evidence,settings=ctx.config,errors=errors,previous=previous)
                result=self._child(ctx,'synthesis:'+str(round_),'ai.generate',arguments)
                value=result['data'].get('json')
                if not isinstance(value,dict):raise ToolError('invalid_synthesis','The AI child returned no structured research document.',usage=Usage())
                return value
            draft=self._stage(ctx,'synthesis:'+str(round_),inputs,synthesize)
            validation=self._stage(ctx,'quality:'+str(round_),{'draft':draft,'plan':plan,'evidence':evidence},
                                   lambda:validate_synthesis(plan,evidence,draft,settings=ctx.config))
            if validation['passed']:break
            errors=validation['errors'];previous=draft
        if validation is None or not validation['passed']:
            ctx.save_state({'research_quality_failure':validation})
            raise ToolError('quality_failed','Bounded correction passes could not establish a complete, grounded customer-specific synthesis.',usage=Usage())
        fixture_label=ctx.config.get('offline_fixture_label','')
        markdown=self._stage(ctx,'render',{'plan':plan,'draft':draft,'validation':validation,'fixture_label':fixture_label},
                             lambda:('> **OFFLINE CONTROLLED FIXTURE:** '+fixture_label+'\n\n' if fixture_label else '')+render_document(plan,evidence,draft,validation))
        prefix='research/'+hashlib.sha256(ctx.action_id.encode()).hexdigest()
        manifest={'version':2,'visibility':'private','fixture_label':fixture_label,'action_id':ctx.action_id,'run_id':ctx.run_id,'job_id':ctx.job_id,
                  'plan':plan,'discovery':discovery,'evidence':evidence,'synthesis':draft,'validation':validation,
                  'child_receipts':ctx.child_executor.summary(),
                  'methodology':'Retrieved literal spans; bounded lexical/date/deduplication and citation checks; explicit sourced facts, inferences and unresolved questions. Not independent verification of publisher truth.'}
        manifest=self._stage(ctx,'manifest-data',{'plan':plan,'draft':draft,'validation':validation},lambda:manifest)
        # Write both with their own zero-cap, authoritative child reservations.
        manifest_result=self._stage(ctx,'manifest',manifest,lambda:self._child(ctx,'manifest','files',
                       {'operation':'create','path':prefix+'/private-evidence.json','text':encoded(manifest)}))
        document_result=self._stage(ctx,'document',{'markdown':markdown},lambda:self._child(ctx,'document','files',
                       {'operation':'create','path':prefix+'/research-brief.md','text':markdown}))
        artifacts=document_result.get('artifacts',[])
        sources=[{key:source[key] for key in ('id','url','title','excerpt')} for source in evidence['sources']]
        span_sources={span['id']:span['source_id'] for span in evidence['spans']}
        source_urls={source['id']:source['url'] for source in evidence['sources']}
        sections=[]
        for section in draft['sections']:
            facts=section['facts'];inferences=section['inferences']
            section_text='\n\n'.join([*[fact['text'] for fact in facts],
                 *['Inference: '+item['text']+' Assumptions: '+item['assumptions'] for item in inferences],
                 *['Unresolved: '+item for item in section['unresolved_questions']]])
            ids=[eid for item in facts+inferences for eid in item['evidence_ids']]
            sections.append({**section,'text':section_text,'source_urls':list(dict.fromkeys(source_urls[span_sources[eid]] for eid in ids))})
        result=ToolResult({'synthesis_version':2,'sources':sources,'sections':sections,'plan':plan,
            'quality':validation,'checks':validation['checks'],
            'document':{'filename':'research-brief.md','media_type':'text/markdown','content':markdown,'artifact':artifacts[0]},
            'evidence_manifest':{'visibility':'private','artifact':manifest_result['artifacts'][0]},
            'child_receipts':ctx.child_executor.summary()},
            Usage(0,details={'basis':'Only orchestration is zero-cost; every paid child has its own reservation and measured receipt.','paid_usage_aggregated_in_parent':False}),
            artifacts=artifacts,references=[{'url':s['url'],'source_id':s['id']} for s in sources])
        ctx.save_state({'research_completed_result':asdict(result)})
        return result

    def reconcile(self,args,ctx):
        saved=ctx.load_state().get('research_completed_result')
        if saved:
            return Reconciliation('completed',ToolResult(saved['data'],Usage(**saved['usage']),saved['artifacts'],saved['references']))
        return Reconciliation('uncertain',detail='Research stages require an authorized execution continuation; reconciliation itself performs no generation.')
