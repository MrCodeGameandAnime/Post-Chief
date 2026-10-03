import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor, require_owner
from postchief.db import get_db
from postchief.models import Publication, SocialAccount, AuditEvent
from postchief.providers.pinterest import PinterestProvider, numeric_id
from postchief.providers.x_credentials import current_x_credentials
from postchief.vault import Vault

router = APIRouter(tags=['Pinterest boards'])


def account(db, actor, account_id, *, lock=False):
    query = select(SocialAccount).where(SocialAccount.id == account_id,
        SocialAccount.org_id == actor.org_id, SocialAccount.provider == 'pinterest',
        SocialAccount.active.is_(True)).execution_options(populate_existing=True)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, 'Active Pinterest account not found')
    return row


async def credentials(request, actor, account_id, provider):
    try:
        return await current_x_credentials(request.app.state.sessions, request.app.state.settings,
            account_id, actor.org_id, provider, provider_name='pinterest')
    except (InvalidToken, ValueError, KeyError):
        raise HTTPException(409, 'Reconnect Pinterest before choosing a board') from None


@router.get('/connections/pinterest/{account_id}/boards')
async def boards(account_id: str, request: Request, bookmark: str | None = Query(default=None, max_length=2048),
                 actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    account(db, actor, account_id)
    async with httpx.AsyncClient(timeout=30) as http:
        provider = PinterestProvider(http, request.app.state.settings)
        creds = await credentials(request, actor, account_id, provider)
        params = {'page_size': 50}
        if bookmark:
            params['bookmark'] = bookmark
        value = await provider.request('GET', 'boards', creds, params=params)
    items = value.get('items', [])
    if not isinstance(items, list):
        raise HTTPException(502, 'Pinterest returned invalid board data')
    return {'boards': [{'id': board['id'], 'name': board['name']} for board in items
        if isinstance(board, dict) and numeric_id(board.get('id')) and isinstance(board.get('name'), str)
        and board.get('privacy') == 'PUBLIC'],
        'bookmark': value.get('bookmark'), 'selected_id': creds.get('board_id'),
        'selected_name': creds.get('board_name')}


class SelectBoard(BaseModel):
    model_config = ConfigDict(extra='forbid')
    board_id: str = Field(pattern=r'^[0-9]{1,30}$')


@router.post('/connections/pinterest/{account_id}/board')
async def select_board(account_id: str, data: SelectBoard, request: Request,
                       actor: Actor = Depends(require_owner), db: Session = Depends(get_db)):
    account(db, actor, account_id)
    async with httpx.AsyncClient(timeout=30) as http:
        provider = PinterestProvider(http, request.app.state.settings)
        creds = await credentials(request, actor, account_id, provider)
        board = await provider.request('GET', 'boards/' + data.board_id, creds)
    if board.get('id') != data.board_id or board.get('privacy') != 'PUBLIC' or not isinstance(board.get('name'), str):
        raise HTTPException(422, 'Choose an accessible public Pinterest board')
    row = account(db, actor, account_id, lock=True)
    vault = Vault(request.app.state.settings.encryption_key.get_secret_value())
    try:
        current = vault.decrypt(row.credentials)
    except (InvalidToken, ValueError):
        raise HTTPException(409, 'Reconnect Pinterest') from None
    if current.get('_refresh_started') or current.get('access_token') != creds['access_token']:
        raise HTTPException(409, 'Pinterest connection changed; reload the board list')
    busy = db.scalar(select(Publication.id).where(Publication.account_id == row.id,
        Publication.org_id == actor.org_id, Publication.status.in_(['pending', 'processing', 'retrying'])).limit(1))
    if busy and current.get('board_id'):
        raise HTTPException(409, 'Wait for delivery or cancel pending Pinterest drafts before changing boards')
    current.update(board_id=data.board_id, board_name=board['name'])
    row.credentials = vault.encrypt(current)
    db.add(AuditEvent(org_id=actor.org_id, actor_id=actor.id, action='connection.pinterest_board',
                     details={'account_id': row.id, 'board_id': data.board_id}))
    db.commit()
    return {'board_id': data.board_id, 'board_name': board['name']}
