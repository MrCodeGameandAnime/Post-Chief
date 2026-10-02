import io
from urllib.parse import urlsplit
from datetime import datetime,timedelta,timezone
import jwt
from PIL import Image
from sqlalchemy import select
from postchief.models import Asset
from postchief.publishing.media import media_for_campaign
from postchief.models import Campaign,SocialAccount


def test_signed_media_rejects_wrong_asset_expiration_and_tampering(client,app):
    data=io.BytesIO(); Image.new('RGB',(2,2)).save(data,'JPEG')
    asset=client.post('/api/assets',files={'file':('photo.jpg',data.getvalue(),'image/jpeg')}).json()
    with app.state.sessions() as db:
        row=db.get(Asset,asset['id'])
        campaign=Campaign(org_id=row.org_id,title='Photo',body='Photo',asset_ids=[row.id],overrides={})
        account=SocialAccount(org_id=row.org_id,provider='instagram',remote_id='ig',name='IG')
        _,media=media_for_campaign(db,campaign,account,app.state.settings)
    parsed=urlsplit(media[0].url)
    url=parsed.path+'?'+parsed.query
    assert client.get(url).content==data.getvalue()
    assert client.get(url.replace(asset['id'],'other')).status_code==403
    token=jwt.encode({'sub':asset['id'],'org':row.org_id,'aud':'post-chief-media','exp':datetime.now(timezone.utc)-timedelta(seconds=1)},app.state.settings.signing_key.get_secret_value(),algorithm='HS256')
    assert client.get(f'/api/media/{asset["id"]}',params={'token':token}).status_code==403
    assert client.get(url[:-20]+'bad').status_code==403
