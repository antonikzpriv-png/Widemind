"""Local creation services remain pluggable; no fixed occupation in the engine."""
from .contracts import Plan
from .services import text

class ArtifactService:
    def __init__(self,name,capability,action,media_type,extension):
        self.name,self.capability,self.action,self.media_type,self.extension=name,capability,action,media_type,extension
    def plan(self,scope,allowance):
        title=text(scope.get('title'),'title',120)
        description=text(scope.get('description','Create a simple useful artifact'),'description',2000)
        return Plan(self.action,{'title':title,'description':description,'filename':self.name+self.extension},0)
    def deliverable(self,scope,result):
        self.plan(scope,0)
        if result.get('media_type')!=self.media_type or not isinstance(result.get('content'),str) or not result['content'].strip():raise ValueError('tool did not produce the agreed artifact')
        if not result.get('checks') or not all(value is True or type(value) is int and value>0 for value in result['checks'].values()):raise ValueError('artifact checks not satisfied')
        return {key:result[key] for key in ('filename','media_type','content','checks')}

LOCAL_SERVICES=[ArtifactService('svg-graphic','graphics','graphics.create','image/svg+xml','.svg'),ArtifactService('software-starter','software','software.create','text/html','.html')]
