from copy import deepcopy
import asyncio
from datetime import datetime,timedelta,timezone
import math
import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select,update,or_
from postchief.models import Publication,SocialAccount,AnalyticsSnapshot,AuditEvent
from postchief.publishing.engine import utc,LEASE
from postchief.providers.registry import get_provider
from postchief.providers.x_credentials import current_x_credentials
from postchief.vault import Vault
from postchief.publishing.links import instagram_permalink
from provider_contracts import ProviderError,ErrorReason

METRICS={'views','reach','impressions','likes','comments','shares','clicks','watch_time','followers','engagement','reactions'}


def normalize(provider,metrics):
    aliases={'replies':'comments','reposts':'shares'} if provider=='bluesky' else {}
    normalized={}
    semantics={}
    for key,value in metrics.items():
        name=aliases.get(key,key)
        if name in METRICS and isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0 and math.isfinite(value):
            normalized[name]=value
            semantics[name]={'replies':'Bluesky replies','reposts':'Bluesky reposts'}.get(key,f'{provider} reported {key}')
            if provider=='x':
                semantics[name]={'comments':'X reported replies','shares':'X reported reposts'}.get(name,f'X reported {name}')
    if provider=='pinterest':
        for name in normalized:
            semantics[name]={'clicks':'Pinterest outbound clicks over the last 30 UTC days',
                'impressions':'Pinterest impressions over the last 30 UTC days'}.get(name,f'Pinterest reported {name}')
    if provider=='youtube':
        for name in normalized:
            semantics[name]={'shares':'YouTube shares over the requested last 30 Pacific dates (reporting can lag)',
                'watch_time':'YouTube watch time in seconds over the requested last 30 Pacific dates (reporting can lag)'}.get(name,f'YouTube lifetime {name}')
    return {'normalized':normalized,'provider_metrics':metrics,'semantics':semantics,'provider':provider}


def due_metrics(sessions,limit=100):
    now=datetime.now(timezone.utc)
    with sessions() as db:
        return list(db.scalars(select(Publication.id).join(SocialAccount,SocialAccount.id==Publication.account_id).where(Publication.status=='published',Publication.provider_id.is_not(None),
            or_(SocialAccount.provider!='x',Publication.analytics_next_at.is_not(None)),
            or_(Publication.analytics_next_at.is_(None),Publication.analytics_next_at<=now),
            or_(Publication.analytics_started_at.is_(None),Publication.analytics_started_at<=now-LEASE))
            .order_by(Publication.analytics_next_at,Publication.created_at).limit(limit)))


async def collect(publication_id,sessions,settings,provider_factory=get_provider):
    now=datetime.now(timezone.utc); vault=Vault(settings.encryption_key.get_secret_value())
    with sessions() as db:
        pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update())
        if not pub or pub.status!='published' or not pub.provider_id: return
        if pub.analytics_next_at and utc(pub.analytics_next_at)>now: return
        if pub.analytics_started_at and utc(pub.analytics_started_at)>now-LEASE: return
        started=pub.analytics_started_at
        claim=db.execute(update(Publication).where(Publication.id==pub.id,
            Publication.analytics_started_at.is_(None) if started is None else Publication.analytics_started_at==started)
            .values(analytics_started_at=now))
        if claim.rowcount!=1: db.rollback(); return
        account=db.get(SocialAccount,pub.account_id)
        if account.provider=='x' and pub.analytics_next_at is None: return
        org_id=pub.org_id; provider_id=pub.provider_id; encrypted=account.credentials
        if account.org_id!=org_id: db.rollback(); return
        account_id=account.id; provider_name=account.provider; active=account.active
        db.commit()
    credentials=None; original=None; metrics=None; error=None
    try:
        if not active or not encrypted: raise ProviderError(ErrorReason.AUTH_REVOKED,'Reconnect the account to collect analytics')
        credentials=vault.decrypt(encrypted); original=deepcopy(credentials)
        async with httpx.AsyncClient(timeout=120) as http:
            provider=provider_factory(provider_name,http,settings)
            if not hasattr(provider,'get_post_metrics'): raise ProviderError(ErrorReason.PERMISSION_MISSING,'Analytics is unavailable for this provider')
            async def read_metrics():
                nonlocal credentials, original
                if provider_name=='x':
                    credentials=await current_x_credentials(sessions,settings,account_id,org_id,provider)
                    original=deepcopy(credentials)
                elif provider_name in ('pinterest','youtube','tiktok'):
                    credentials=await current_x_credentials(sessions,settings,account_id,org_id,provider,provider_name=provider_name)
                    original=deepcopy(credentials)
                elif credentials.get('expires_at') and utc(datetime.fromisoformat(credentials['expires_at']))<now+timedelta(days=1):
                    if hasattr(provider,'refresh_auth'): await provider.refresh_auth(credentials)
                    elif utc(datetime.fromisoformat(credentials['expires_at']))<=now: raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect to collect analytics')
                return await provider.get_post_metrics(credentials,provider_id)
            metrics=normalize(provider_name,await asyncio.wait_for(read_metrics(),timeout=270))
    except ProviderError as value: error=value
    except (InvalidToken,ValueError): error=ProviderError(ErrorReason.AUTH_REVOKED,'Stored credentials are invalid; reconnect this account')
    except Exception: error=ProviderError(ErrorReason.UNKNOWN,'Analytics collection failed; try again later',retryable=True)
    with sessions() as db:
        pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update())
        if not pub or utc(pub.analytics_started_at)!=now: return
        if credentials is not None and credentials!=original:
            expires=utc(datetime.fromisoformat(credentials['expires_at'])) if credentials.get('expires_at') else None
            db.execute(update(SocialAccount).where(SocialAccount.id==account_id,SocialAccount.credentials==encrypted,SocialAccount.active.is_(True))
                .values(credentials=vault.encrypt(credentials),expires_at=expires))
        pub.analytics_started_at=None
        pub.analytics_error=error.to_dict() if error else None
        pub.analytics_next_at=datetime.now(timezone.utc)+timedelta(minutes=10 if error and error.retryable else 1440 if error else 60)
        if provider_name=='x' and not (error and error.retryable):
            pub.analytics_next_at=None  # Paid reads run only on an explicit refresh request.
        if metrics is not None:
            if provider_name=='instagram' and not pub.provider_url:
                pub.provider_url=instagram_permalink(metrics.get('provider_metrics',{}).get('provider',{}).get('permalink'))
            db.add(AnalyticsSnapshot(org_id=org_id,publication_id=pub.id,metrics=metrics))
        db.add(AuditEvent(org_id=org_id,actor_id='worker',action='analytics.failed' if error else 'analytics.collected',details={'publication_id':pub.id,'provider':provider_name}))
        db.commit()
