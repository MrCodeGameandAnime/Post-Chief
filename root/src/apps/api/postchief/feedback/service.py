import hashlib
import json
import re
from difflib import unified_diff
from sqlalchemy import select
from postchief.models import AnalyticsSnapshot
from postchief.campaigns.service import serialize_campaign,iso
from postchief.github.service import GitHubError,GitHubService


def merge_block(existing,id,content):
    start=f'<!-- postchief:{id}:start -->';end=f'<!-- postchief:{id}:end -->'
    block=start+'\n'+content.rstrip()+'\n'+end
    starts=list(re.finditer(r'(?m)^'+re.escape(start)+r'\r?$',existing))
    ends=list(re.finditer(r'(?m)^'+re.escape(end)+r'\r?$',existing))
    if starts or ends:
        if len(starts)!=1 or len(ends)!=1 or ends[0].start()<starts[0].start():
            raise GitHubError(409,'Generated feedback markers conflict; repair them before writing')
        return existing[:starts[0].start()]+block+existing[ends[0].end():]
    return existing+('\n' if existing.endswith('\n') else '\n\n')+block+'\n' if existing else block+'\n'


def fenced(value):
    content=json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True)
    fence='`'*max(3,max((len(x) for x in re.findall(r'`+',content)),default=0)+1)
    return fence+'json\n'+content+'\n'+fence


def report(db,campaign):
    data=serialize_campaign(db,campaign)
    metrics=[]
    for pub in data['publications']:
        row=db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.publication_id==pub['id'],AnalyticsSnapshot.org_id==campaign.org_id)
            .order_by(AnalyticsSnapshot.created_at.desc()).limit(1))
        metrics.append({'publication_id':pub['id'],'provider':pub['provider'],'account':pub['account_name'],
            'collected_at':iso(row.created_at) if row else None,'normalized':row.metrics.get('normalized',{}) if row else {},
            'semantics':row.metrics.get('semantics',{}) if row else {},'available':row is not None})
    # Provider state/credentials and signed media URLs are deliberately excluded.
    return data,metrics


def post_path(campaign):
    path=campaign.github_path or f'posts/{campaign.id}.md'
    GitHubService.content_path(path)
    if not path.startswith('posts/') or not path.endswith('.md') or not all(path.split('/')):
        raise GitHubError(422,'Post records must be Markdown files under posts/')
    return path


async def preview(db,campaign,connection,service):
    data,metrics=report(db,campaign);path=post_path(campaign)
    blocks={path:'## Post Chief publication record\n\n'+fenced(data),
        'docs/CONTENT_LEDGER.md':'## Campaign '+campaign.id+'\n\n'+fenced({key:data[key] for key in ('id','title','status','scheduled_at','publications')}),
        'docs/ANALYTICS.md':'## Campaign '+campaign.id+' · native metrics\n\n'+fenced(metrics)}
    snapshot=await service.feedback_snapshot(connection.installation_id,connection.workspace,list(blocks))
    files={path:merge_block(snapshot['files'][path]['content'],campaign.id,block) for path,block in blocks.items()}
    digest=hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    unchanged=all(files[path]==snapshot['files'][path]['content'] for path in files)
    reviewed=[]
    for path,content in files.items():
        old=snapshot['files'][path]['content']
        lines=list(unified_diff(old.splitlines(),content.splitlines(),fromfile=path,tofile=path,lineterm=''))
        reviewed.append({'path':path,'content':content,'diff':'\n'.join(lines),
            'change':'unchanged' if old==content else 'updated' if snapshot['files'][path].get('exists',bool(old)) else 'created',
            'additions':sum(line.startswith('+') for line in lines[2:]),
            'deletions':sum(line.startswith('-') for line in lines[2:])})
    return {'campaign_id':campaign.id,'revision':campaign.revision,'workspace':connection.workspace,'branch':snapshot['branch'],
        'base_commit':snapshot['head'],'digest':digest,'unchanged':unchanged,'files':reviewed},snapshot,files
