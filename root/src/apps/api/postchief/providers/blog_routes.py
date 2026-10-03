import hashlib
import secrets
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from postchief.auth import Actor, require_owner
from postchief.db import get_db
from postchief.providers.routes import save_connection
from postchief.providers.blog import source_url, fetch_public, parse_feed, discover

router = APIRouter(tags=['Blog source connections'])


class Source(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=8, max_length=2048)


@router.post('/connections/blog/discover')
async def discover_feed(data: Source, actor: Actor = Depends(require_owner)):
    return {'feeds': await discover(data.url)}


@router.post('/connections/blog', status_code=201)
async def connect(data: Source, request: Request, actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    url = source_url(data.url)
    body, final = await fetch_public(url)
    feed = parse_feed(body, final)
    # No feed data grants permissions or controls its connection identity.
    identity = hashlib.sha256(url.encode()).hexdigest()
    credentials = {'id': identity, 'source_url': url, '_connection_nonce': secrets.token_urlsafe(32)}
    label = (feed['title'][:150] + ' · ' + urlsplit(url).hostname)[:200]
    return save_connection(db, request.app.state.settings, actor, 'blog', identity, label, credentials)
