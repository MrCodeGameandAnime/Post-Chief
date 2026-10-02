from sqlalchemy import select
from datetime import datetime,timedelta,timezone
from postchief.models import AgentKey,Approval,SocialAccount,Organization
from postchief.auth import digest
from test_publishing import setup_campaign


def key(client,scopes):
    response=client.post('/api/agent/keys',json={'name':'Maintainer','scopes':scopes})
    assert response.status_code==201
    return response.json()


def test_agent_keys_are_scoped_hashed_once_and_revocable(app,client):
    issued=key(client,['campaigns:read'])
    headers={'Authorization':'Bearer '+issued['token']}
    with app.state.sessions() as db:
        stored=db.get(AgentKey,issued['id']);assert stored.token_hash==digest(issued['token'])
        assert issued['token'] not in str(stored.__dict__)
    assert 'token' not in client.get('/api/agent/keys').json()[0]
    assert client.get('/api/campaigns',headers=headers).status_code==200
    assert client.post('/api/campaigns',headers=headers,json={'title':'Release','body':'Hello','account_ids':['id']}).status_code==403
    assert client.post('/api/agent/keys',headers=headers,json={'name':'Escalate','scopes':['campaigns:publish']}).status_code==403
    assert client.delete('/api/agent/keys/'+issued['id']).status_code==200
    assert client.get('/api/campaigns',headers=headers).status_code==401


def test_publish_approval_binds_payload_and_is_single_use(app,client):
    campaign=setup_campaign(app,client);issued=key(client,['campaigns:read','campaigns:publish'])
    headers={'Authorization':'Bearer '+issued['token']}
    action={'action':'campaign.publish','target_id':campaign['id'],'data':{}}
    assert client.post('/api/campaigns/'+campaign['id']+'/publish',headers=headers).status_code==403
    challenge=client.post('/api/agent/actions',headers=headers,json=action)
    assert challenge.status_code==428
    id=challenge.json()['detail']['approval_id']
    assert client.post('/api/approvals/'+id+'/approve').status_code==200
    execution={**action,'approval_id':id}
    assert client.post('/api/agent/actions',headers=headers,json=execution).status_code==200
    assert client.post('/api/agent/actions',headers=headers,json=execution).status_code==409
    with app.state.sessions() as db:assert db.get(Approval,id).status=='consumed'


def test_approval_cannot_publish_changed_copy_and_disabled_policy_wins(app,client):
    campaign=setup_campaign(app,client);issued=key(client,['campaigns:publish'])
    headers={'Authorization':'Bearer '+issued['token']}
    action={'action':'campaign.publish','target_id':campaign['id'],'data':{}}
    id=client.post('/api/agent/actions',headers=headers,json=action).json()['detail']['approval_id']
    client.post('/api/approvals/'+id+'/approve')
    assert client.patch('/api/campaigns/'+campaign['id'],json={'revision':1,'body':'Changed copy'}).status_code==200
    assert client.post('/api/agent/actions',headers=headers,json={**action,'approval_id':id}).status_code==409
    assert client.put('/api/settings/autonomy',json={'policies':{'campaigns:publish':'DISABLED'}}).status_code==200
    assert client.post('/api/agent/actions',headers=headers,json=action).status_code==403


def test_agent_run_summaries_and_audit_are_visible_without_secrets(client):
    issued=key(client,['agent:report','audit:read'])
    headers={'Authorization':'Bearer '+issued['token']}
    assert client.post('/api/agent/runs',headers=headers,json={'summary':'Reviewed recent releases; no content needed.','status':'completed'}).status_code==201
    assert client.get('/api/agent/runs').json()[0]['summary'].startswith('Reviewed')
    events=client.get('/api/audit').json()
    assert any(e['action']=='agent.request' for e in events)
    assert issued['token'] not in str(events)


def test_expired_or_another_agents_approval_cannot_execute(app,client):
    campaign=setup_campaign(app,client);first=key(client,['campaigns:publish']);second=key(client,['campaigns:publish'])
    headers={'Authorization':'Bearer '+first['token']};other={'Authorization':'Bearer '+second['token']}
    action={'action':'campaign.publish','target_id':campaign['id'],'data':{}}
    id=client.post('/api/agent/actions',headers=headers,json=action).json()['detail']['approval_id']
    client.post('/api/approvals/'+id+'/approve')
    assert client.post('/api/agent/actions',headers=other,json={**action,'approval_id':id}).status_code==409
    with app.state.sessions() as db:db.get(Approval,id).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);db.commit()
    assert client.post('/api/agent/actions',headers=headers,json={**action,'approval_id':id}).status_code==409


def test_failed_action_rolls_back_approval_consumption(app,client):
    campaign=setup_campaign(app,client);issued=key(client,['campaigns:publish'])
    headers={'Authorization':'Bearer '+issued['token']};action={'action':'campaign.publish','target_id':campaign['id'],'data':{}}
    id=client.post('/api/agent/actions',headers=headers,json=action).json()['detail']['approval_id']
    client.post('/api/approvals/'+id+'/approve')
    with app.state.sessions() as db:db.get(SocialAccount,campaign['publications'][0]['account_id']).active=False;db.commit()
    assert client.post('/api/agent/actions',headers=headers,json={**action,'approval_id':id}).status_code==409
    with app.state.sessions() as db:assert db.get(Approval,id).status=='approved'


def test_agent_cannot_read_or_mutate_another_organization(app,client):
    campaign=setup_campaign(app,client)
    with app.state.sessions() as db:
        org=Organization(name='Other');db.add(org);db.flush()
        db.add(AgentKey(org_id=org.id,name='Foreign',token_hash=digest('other-agent-token'),scopes=['campaigns:read','campaigns:publish']));db.commit()
    headers={'Authorization':'Bearer other-agent-token'}
    assert client.get('/api/campaigns/'+campaign['id'],headers=headers).status_code==404
    assert client.post('/api/agent/actions',headers=headers,json={'action':'campaign.publish','target_id':campaign['id']}).status_code==404
