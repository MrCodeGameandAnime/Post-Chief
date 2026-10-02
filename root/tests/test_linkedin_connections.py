from sqlalchemy import select
from postchief.models import User,SocialAccount
from postchief.providers.linkedin_routes import get_linkedin_provider
from postchief.vault import Vault


class FakeLinkedIn:
    async def organizations(self,credentials):
        assert credentials['author']=='urn:li:person:owner'
        return [{'id':'urn:li:organization:1','name':'404 Builds'}]


def test_company_connection_requires_an_authorized_page_and_retains_encryption(client,app):
    app.dependency_overrides[get_linkedin_provider]=lambda:FakeLinkedIn()
    with app.state.sessions() as db:
        owner=db.scalar(select(User))
        row=SocialAccount(org_id=owner.org_id,provider='linkedin',remote_id='urn:li:person:owner',name='Owner',credentials=Vault(app.state.settings.encryption_key.get_secret_value()).encrypt({'author':'urn:li:person:owner','access_token':'secret'}))
        db.add(row); db.commit(); id=row.id
    available=client.get(f'/api/connections/linkedin/{id}/organizations')
    assert available.status_code==200 and available.json()[0]['name']=='404 Builds'
    denied=client.post(f'/api/connections/linkedin/{id}/organizations',json={'organization':'urn:li:organization:2'})
    assert denied.status_code==403
    connected=client.post(f'/api/connections/linkedin/{id}/organizations',json={'organization':'urn:li:organization:1'})
    assert connected.status_code==201 and 'secret' not in connected.text
    with app.state.sessions() as db:
        company=db.get(SocialAccount,connected.json()['id'])
        assert company.remote_id=='urn:li:organization:1' and 'secret' not in company.credentials
