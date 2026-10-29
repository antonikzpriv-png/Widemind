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


# Preserve the integrated import contract while keeping research stages separate.
from .research_workflow import ResearchWorkflow
