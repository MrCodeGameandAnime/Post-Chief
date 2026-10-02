import pytest
from sqlalchemy import select
from postchief.models import User,GitHubConnection
from postchief.github.routes import get_service
from postchief.github.service import GitHubError
from postchief.feedback.service import merge_block
from test_publishing import setup_campaign


class FeedbackService:
    head='a'*40
    writes=0
    def __init__(self):self.files={'docs/CONTENT_LEDGER.md':'# Ledger\nOwner notes remain.\n'}
    async def feedback_snapshot(self,installation,repo,paths):
        assert repo=='owner/social'
        return {'branch':'main','head':self.head,'tree':'tree','files':{path:{'content':self.files.get(path,''),'mode':'100644'} for path in paths}}
    async def commit_feedback(self,installation,repo,snapshot,files,message):
        if snapshot['head']!=self.head:raise GitHubError(409,'Branch changed')
        self.files.update(files);self.head='b'*40;self.writes+=1
        return {'sha':self.head,'changed':True}


def workspace(app,service):
    with app.state.sessions() as db:
        owner=db.scalar(select(User));db.add(GitHubConnection(org_id=owner.org_id,installation_id=1,workspace='owner/social'));db.commit()
    app.dependency_overrides[get_service]=lambda:service


def test_feedback_preserves_notes_and_repeated_sync_does_not_duplicate_blocks(app,client):
    campaign=setup_campaign(app,client);service=FeedbackService();workspace(app,service)
    preview=client.get(f'/api/feedback/campaigns/{campaign["id"]}/preview')
    assert preview.status_code==200
    data=preview.json();assert len(data['files'])==3
    assert 'Owner notes remain.' in next(row['content'] for row in data['files'] if row['path']=='docs/CONTENT_LEDGER.md')
    payload={key:data[key] for key in ('revision','digest','base_commit')}
    assert client.post(f'/api/feedback/campaigns/{campaign["id"]}/sync',json=payload).status_code==200
    assert client.post(f'/api/feedback/campaigns/{campaign["id"]}/sync',json=payload).status_code==200
    assert service.writes==1
    assert service.files['docs/CONTENT_LEDGER.md'].count('<!-- postchief:'+campaign['id']+':start -->')==1


def test_feedback_rejects_unreviewed_changes_or_dangerous_post_path(app,client):
    campaign=setup_campaign(app,client);service=FeedbackService();workspace(app,service)
    data=client.get(f'/api/feedback/campaigns/{campaign["id"]}/preview').json()
    payload={key:data[key] for key in ('revision','digest','base_commit')}
    client.patch(f'/api/campaigns/{campaign["id"]}',json={'revision':1,'body':'Changed'})
    assert client.post(f'/api/feedback/campaigns/{campaign["id"]}/sync',json=payload).status_code==409
    client.patch(f'/api/campaigns/{campaign["id"]}',json={'revision':2,'github_path':'.github/workflows/overwrite.yml'})
    assert client.get(f'/api/feedback/campaigns/{campaign["id"]}/preview').status_code==422
    assert service.writes==0


def test_marker_conflicts_are_not_silently_overwritten():
    with pytest.raises(GitHubError):merge_block('Owner text\n<!-- postchief:id:start -->\nBroken','id','New')


def test_agent_feedback_requires_exact_owner_approval(app,client):
    from test_agents import key
    campaign=setup_campaign(app,client);service=FeedbackService();workspace(app,service)
    issued=key(client,['campaigns:read','analytics:read','github:read','github:write'])
    headers={'Authorization':'Bearer '+issued['token']}
    data=client.get(f'/api/feedback/campaigns/{campaign["id"]}/preview',headers=headers).json()
    payload={name:data[name] for name in ('revision','digest','base_commit')}
    assert client.post(f'/api/feedback/campaigns/{campaign["id"]}/sync',headers=headers,json=payload).status_code==403
    action={'action':'feedback.sync','target_id':campaign['id'],'data':payload}
    challenge=client.post('/api/agent/actions',headers=headers,json=action)
    assert challenge.status_code==428
    approval_id=challenge.json()['detail']['approval_id']
    assert client.post('/api/approvals/'+approval_id+'/approve').status_code==200
    execution={**action,'approval_id':approval_id}
    assert client.post('/api/agent/actions',headers=headers,json=execution).status_code==200
    assert client.post('/api/agent/actions',headers=headers,json=execution).status_code==409
    assert service.writes==1
