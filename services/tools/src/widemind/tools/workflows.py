"""Real bounded business deliverables composed from existing confined adapters."""
from __future__ import annotations
from dataclasses import replace
import hashlib
import html
from urllib.parse import urlsplit
from .contracts import Quote, ToolSpec, ToolError, ToolResult, Unavailable, Usage
from .files import FilesAdapter, _local_receipt, _reconcile_local
from .graphics import GraphicsAdapter
from .research import ResearchAdapter


def text(value,name,maximum=2000):
    if not isinstance(value,str) or not value.strip() or len(value.encode())>maximum:raise ToolError('invalid_input',name+' must be bounded nonempty text')
    return value.strip()


def artifact(result,ctx,filename,media_type,checks):
    record=result.artifacts[0];content=ctx.workspace.read_bytes(record['path'],max_bytes=1_000_000).decode('utf-8')
    return ToolResult({'filename':filename,'media_type':media_type,'content':content,'checks':checks,'artifact':record},result.usage,artifacts=result.artifacts)


class GraphicWorkflow:
    spec=ToolSpec('graphics.create','Create an actual SVG graphic with safe primitives and requested text.','local')
    def availability(self,config,secrets):return {'available':config.get('enabled',False) is True,'provider':'local'}
    def arguments(self,args,path):
        title=text(args.get('title'),'title',300);description=args.get('description','')
        if not isinstance(description,str) or len(description.encode())>2000:raise ToolError('invalid_input','description exceeds limit')
        width=args.get('width',800);height=args.get('height',600)
        elements=args.get('elements',[{'type':'rect','x':0,'y':0,'width':width,'height':height,'fill':'#14231b'},{'type':'text','x':40,'y':100,'text':title,'fill':'#c8f7a8','font_size':36},{'type':'text','x':40,'y':160,'text':description,'fill':'#ffffff','font_size':20}])
        return {'path':path,'format':'svg','width':width,'height':height,'elements':elements,'background':args.get('background','#14231b')}
    def quote(self,args,config):return GraphicsAdapter().quote(self.arguments(args,'preview.svg'),config)
    def execute(self,args,ctx):
        filename=args.get('filename','graphic.svg');path='deliveries/'+hashlib.sha256(ctx.action_id.encode()).hexdigest()+'/'+filename
        result=GraphicsAdapter().execute(self.arguments(args,path),ctx)
        result=artifact(result,ctx,filename,'image/svg+xml',{'valid_svg':True,'title_present':True,'width':args.get('width',800),'height':args.get('height',600)})
        _local_receipt(args,ctx,result);return result
    def reconcile(self,args,ctx):return _reconcile_local(args,ctx)


class SoftwareWorkflow:
    spec=ToolSpec('software.create','Create an actual static website source file without executing host code.','local')
    def availability(self,config,secrets):return {'available':config.get('enabled',False) is True,'provider':'local'}
    def render(self,args):
        title=text(args.get('title'),'title',300);description=text(args.get('description','A useful static website starter.'),'description',4000)
        features=args.get('features',[])
        if not isinstance(features,list) or len(features)>20:raise ToolError('invalid_input','features must contain up to 20 text items')
        features=[text(item,'feature',500) for item in features]
        esc=html.escape
        return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>body{font:18px system-ui;max-width:800px;margin:auto;padding:40px;color:#183424;background:#f4f8f3}h1{font-size:44px}li{padding:10px}</style></head><body><main><h1>'+esc(title)+'</h1><p>'+esc(description)+'</p><ul>'+''.join('<li>'+esc(item)+'</li>' for item in features)+'</ul></main></body></html>'
    def quote(self,args,config):
        content=self.render(args)
        if len(content.encode())>config.get('max_output_bytes',128000):raise ToolError('output_limit','Generated source exceeds output cap')
        return Quote(limits={'no_code_execution':True,'renderer':'escaped-static-html'})
    def execute(self,args,ctx):
        filename=args.get('filename','starter.html');path='deliveries/'+hashlib.sha256(ctx.action_id.encode()).hexdigest()+'/'+filename
        result=FilesAdapter().execute({'operation':'create','path':path,'text':self.render(args)},ctx)
        result=artifact(result,ctx,filename,'text/html',{'static_html':True,'escaped':True,'source_created':True})
        _local_receipt(args,ctx,result);return result
    def reconcile(self,args,ctx):return _reconcile_local(args,ctx)


class ResearchWorkflow:
    spec=ToolSpec('research.collect','Collect real excerpts and citations from operator-curated public sources.')
    def availability(self,config,secrets):
        urls=config.get('source_urls',[])
        return {'available':config.get('enabled',False) is True and bool(urls),'reason':'Configure source_urls and explicit network policy for research collection'}
    def validate(self,args,config):
        text(args.get('topic'),'topic',500);sections=args.get('sections');minimum=args.get('min_sources',2);urls=config.get('source_urls',[])
        if not isinstance(sections,list) or not 1<=len(sections)<=8 or any(not isinstance(item,str) or not item.strip() or len(item)>100 for item in sections):raise ToolError('invalid_input','Research requires 1–8 bounded section headings')
        if type(minimum) is not int or not 1<=minimum<=10 or not isinstance(urls,list) or len(set(urls))<minimum:raise Unavailable('Configure enough distinct owner-curated source URLs; sources are not fabricated')
        for url in urls:
            parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise Unavailable('Research source URLs must use public HTTPS')
        return sections,urls[:10]
    def quote(self,args,config):
        sections,urls=self.validate(args,config)
        for url in urls:ResearchAdapter().quote({'url':url,'max_text_chars':3000},config)
        return Quote(provider='public_http',limits={'max_sources':len(urls),'source_mode':'owner-curated actual excerpts; no model claims'})
    def execute(self,args,ctx):
        sections,urls=self.validate(args,ctx.config);sources=[];references=[]
        for url in urls:
            result=ResearchAdapter().execute({'url':url,'max_text_chars':3000},ctx)
            if result.data.get('status')!=200 or not result.data.get('text'):raise ToolError('insufficient_sources','A configured source did not return usable content')
            sources.append({'url':result.data['url'],'title':result.data.get('title') or result.data['url'],'excerpt':result.data['text'][:3000]});references.extend(result.references)
        excerpt='Source excerpts (not an inferred synthesis; verify relevance to the requested heading):\n\n'+'\n\n'.join(source['title']+': '+source['excerpt'] for source in sources)
        result=ToolResult({'sources':sources,'sections':[{'heading':heading,'text':excerpt,'source_urls':[source['url'] for source in sources]} for heading in sections]},Usage(0,details={'network_compute':'owner-subsidized, unallocated'}),references=references)
        # Read-only collection can be recovered from its durable completed record.
        ctx.save_state({'research_workflow_result':{'data':dict(result.data),'references':references}});return result
    def reconcile(self,args,ctx):
        from .contracts import Reconciliation
        value=ctx.load_state().get('research_workflow_result')
        return Reconciliation('completed',ToolResult(value['data'],references=value['references'])) if value else Reconciliation('uncertain',detail='Collection has no complete durable receipt')
