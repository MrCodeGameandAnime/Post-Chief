from datetime import datetime,timedelta,timezone
from pathlib import Path
import jwt
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.db import get_db
from postchief.models import Asset
from provider_contracts import Media,ProviderError,ErrorReason

router=APIRouter(tags=['Provider media delivery'])


def asset_path(asset,settings):
    root=Path(settings.media_dir).resolve()
    path=(root/asset.storage_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ProviderError(ErrorReason.MEDIA_INVALID,'Stored media is unavailable')
    return path


def media_for_campaign(db,campaign,account,settings):
    override=campaign.overrides.get(account.provider,{})
    ids=override.get('asset_ids',campaign.asset_ids)
    media=[]
    for id in ids:
        asset=db.scalar(select(Asset).where(Asset.id==id,Asset.org_id==campaign.org_id))
        if not asset: raise ProviderError(ErrorReason.MEDIA_INVALID,'Campaign media is unavailable')
        claims={'sub':asset.id,'org':asset.org_id,'aud':'post-chief-media','exp':datetime.now(timezone.utc)+timedelta(hours=2)}
        token=jwt.encode(claims,settings.signing_key.get_secret_value(),algorithm='HS256')
        url=settings.public_url.rstrip('/')+'/api/media/'+asset.id+'?token='+token
        media.append(Media(str(asset_path(asset,settings)),asset.mime_type,asset.byte_size,asset.details.get('alt_text',''),url))
    return override.get('body',campaign.body),media


@router.get('/media/{asset_id}')
def signed_media(asset_id:str,token:str,request:Request,db:Session=Depends(get_db)):
    try:
        claims=jwt.decode(token,request.app.state.settings.signing_key.get_secret_value(),algorithms=['HS256'],audience='post-chief-media',options={'require':['sub','org','exp']})
    except jwt.PyJWTError: raise HTTPException(403,'Media link is invalid or expired')
    if claims['sub']!=asset_id: raise HTTPException(403,'Media link does not match this asset')
    asset=db.scalar(select(Asset).where(Asset.id==asset_id,Asset.org_id==claims['org']))
    if not asset: raise HTTPException(404,'Media not found')
    if asset.mime_type=='image/svg+xml': raise HTTPException(422,'SVG must be converted before public delivery')
    return FileResponse(asset_path(asset,request.app.state.settings),media_type=asset.mime_type,
        headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, max-age=60','Referrer-Policy':'no-referrer'})
