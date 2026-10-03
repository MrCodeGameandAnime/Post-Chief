import asyncio
from copy import deepcopy
from cryptography.fernet import InvalidToken
from datetime import datetime,timedelta,timezone
import httpx
from sqlalchemy import select,update,or_
from postchief.models import Campaign,Publication,SocialAccount,AuditEvent
from postchief.vault import Vault
from postchief.providers.registry import get_provider
from postchief.providers.x_credentials import current_x_credentials
from postchief.publishing.media import media_for_campaign
from provider_contracts import PublicationPending,ProviderError,ErrorReason

LEASE=timedelta(minutes=10)
MAX_RETRIES=5


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def due_publications(sessions,limit=100):
    now=datetime.now(timezone.utc)
    with sessions() as db:
        return list(db.scalars(select(Publication.id).join(Campaign,Campaign.id==Publication.campaign_id).where(
            Campaign.status.in_(['scheduled','publishing','partial']),Campaign.scheduled_at<=now,
            or_(Publication.status.in_(['pending','retrying']) & or_(Publication.next_attempt_at.is_(None),Publication.next_attempt_at<=now),
                (Publication.status=='processing') & (Publication.started_at<=now-LEASE)))
            .order_by(Campaign.scheduled_at,Publication.created_at).limit(limit)))


def campaign_status(db,campaign):
    statuses=list(db.scalars(select(Publication.status).where(Publication.campaign_id==campaign.id)))
    if all(s=='published' for s in statuses): campaign.status='published'
    elif any(s in ('pending','retrying','processing') for s in statuses): campaign.status='publishing'
    elif 'awaiting_owner' in statuses: campaign.status='awaiting_owner'
    elif 'published' in statuses: campaign.status='partial'
    elif all(s=='cancelled' for s in statuses): campaign.status='draft'
    else: campaign.status='failed'


def state_for(pub,vault):
    return vault.decrypt(pub.provider_state['sealed']) if pub.provider_state.get('sealed') else dict(pub.provider_state)


async def execute_publication(publication_id,sessions,settings,provider_factory=get_provider):
    now=datetime.now(timezone.utc); vault=Vault(settings.encryption_key.get_secret_value())
    async with httpx.AsyncClient(timeout=120) as http:
        with sessions() as db:
            initial=db.get(Publication,publication_id)
            if not initial: return
            campaign=db.scalar(select(Campaign).where(Campaign.id==initial.campaign_id).with_for_update())
            pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update().execution_options(populate_existing=True))
            if campaign.status not in ('scheduled','publishing','partial') or not campaign.scheduled_at or utc(campaign.scheduled_at)>now: return
            if pub.status in ('published','failed','cancelled'): return
            if pub.next_attempt_at and utc(pub.next_attempt_at)>now: return
            account=db.get(SocialAccount,pub.account_id)
            if not account or account.org_id!=campaign.org_id or pub.org_id!=campaign.org_id:
                return
            provider=provider_factory(account.provider,http,settings)
            try: state=state_for(pub,vault)
            except (InvalidToken,ValueError,TypeError):
                pub.status='failed'
                pub.error=ProviderError(ErrorReason.UNKNOWN,'Stored publication state cannot be read; restore the encryption key or reconcile this attempt',uncertain=not provider.idempotent).to_dict()
                db.add(AuditEvent(org_id=pub.org_id,actor_id='worker',action='publication.state_invalid',details={'publication_id':pub.id}))
                campaign_status(db,campaign); db.commit(); return
            if pub.status=='processing':
                if pub.started_at and utc(pub.started_at)>now-LEASE: return
                if not provider.idempotent and state.get('phase')=='publish_intent':
                    pub.status='failed'; pub.error=ProviderError(ErrorReason.PROVIDER_ERROR,'Worker stopped during a public write; reconcile the result',uncertain=True).to_dict()
                    db.add(AuditEvent(org_id=pub.org_id,actor_id='worker',action='publication.reconcile_required',details={'publication_id':pub.id}))
                    campaign_status(db,campaign); db.commit(); return
            previous_started=pub.started_at
            claim=db.execute(update(Publication).where(Publication.id==pub.id,Publication.status==pub.status,
                Publication.started_at.is_(None) if previous_started is None else Publication.started_at==previous_started)
                .values(status='processing',started_at=now,attempts=Publication.attempts+1))
            if claim.rowcount!=1: db.rollback(); return
            campaign.status='publishing'
            db.commit()
            encrypted_credentials=account.credentials
            account_id=account.id; org_id=account.org_id
            body=campaign.overrides.get(account.provider,{}).get('body',campaign.body)
            try:
                if account.provider=='youtube':
                    state.setdefault('youtube',provider.validate_options(campaign.overrides.get('youtube',{}).get('youtube'),campaign.title))
                if account.provider=='tiktok':
                    state.setdefault('tiktok',provider.validate_options(campaign.overrides.get('tiktok',{}).get('tiktok')))
                if not account.active or not account.credentials: raise ProviderError(ErrorReason.AUTH_REVOKED,'Reconnect this account')
                credentials=vault.decrypt(account.credentials)
                original_credentials=deepcopy(credentials)
                body,media=media_for_campaign(db,campaign,account,settings)
            except ProviderError as error:
                credentials=None; original_credentials=None; media=[]; preparation_error=error
            except (InvalidToken,ValueError):
                credentials=None; original_credentials=None; media=[]
                preparation_error=ProviderError(ErrorReason.AUTH_REVOKED,'Stored credentials are invalid; reconnect this account')
            else: preparation_error=None
        # No database transaction spans provider I/O.
        result=None; error=None; pending=None
        state.setdefault('_execution_started',now.isoformat())
        try:
            if preparation_error: raise preparation_error
            if now-datetime.fromisoformat(state['_execution_started'])>timedelta(hours=24):
                raise ProviderError(ErrorReason.MEDIA_INVALID,'Publication processing exceeded 24 hours; review media')
            refresh=getattr(provider,'refresh_auth',None)
            if account.provider=='x':
                credentials=await current_x_credentials(sessions,settings,account_id,org_id,provider)
                original_credentials=deepcopy(credentials)
            elif account.provider in ('pinterest','youtube','tiktok'):
                credentials=await current_x_credentials(sessions,settings,account_id,org_id,provider,provider_name=account.provider)
                original_credentials=deepcopy(credentials)
            elif credentials.get('expires_at') and utc(datetime.fromisoformat(credentials['expires_at']))<now+timedelta(days=1):
                if refresh: await refresh(credentials)
                elif utc(datetime.fromisoformat(credentials['expires_at']))<=now: raise ProviderError(ErrorReason.AUTH_EXPIRED,'Reconnect this account')
            result=await asyncio.wait_for(provider.publish(credentials,body,media,publication_id,state),timeout=150)
        except PublicationPending as value:
            pending=value
        except ProviderError as value:
            error=value
        except Exception:
            uncertain=not provider.idempotent and state.get('phase')=='publish_intent'
            error=ProviderError(ErrorReason.UNKNOWN,'Publication failed unexpectedly; review this attempt',retryable=provider.idempotent,uncertain=uncertain)
        with sessions() as db:
            campaign=db.scalar(select(Campaign).where(Campaign.id==initial.campaign_id).with_for_update())
            pub=db.scalar(select(Publication).where(Publication.id==publication_id).with_for_update())
            if not pub or pub.status!='processing' or utc(pub.started_at)!=now: return
            if credentials is not None and credentials!=original_credentials:
                expires=utc(datetime.fromisoformat(credentials['expires_at'])) if credentials.get('expires_at') else None
                db.execute(update(SocialAccount).where(SocialAccount.id==account_id,SocialAccount.org_id==org_id,
                    SocialAccount.credentials==encrypted_credentials,SocialAccount.active.is_(True))
                    .values(credentials=vault.encrypt(credentials),expires_at=expires))
            if result:
                state.update(result.state); state.pop('_retry_count',None)
                if result.metadata: state['_public_metadata']=result.metadata
                pub.status='published'; pub.provider_id=result.provider_id; pub.provider_url=result.url
                pub.published_at=datetime.now(timezone.utc); pub.error=None; pub.next_attempt_at=None
            elif pending:
                state.update(pending.state)
                pub.status=pending.status if pending.status=='awaiting_owner' else 'retrying'; pub.error=None
                pub.next_attempt_at=None if pub.status=='awaiting_owner' else datetime.now(timezone.utc)+timedelta(seconds=max(1,min(pending.retry_after,300)))
            else:
                pub.error=error.to_dict(); retries=int(state.get('_retry_count',0))+1; state['_retry_count']=retries
                if error.retryable and not error.uncertain and retries<=MAX_RETRIES:
                    pub.status='retrying'; pub.next_attempt_at=datetime.now(timezone.utc)+timedelta(seconds=min(3600,30*2**(retries-1)))
                else: pub.status='failed'; pub.next_attempt_at=None
            pub.provider_state={'sealed':vault.encrypt(state)}
            if state.get('_public_metadata'):
                previous_metadata=db.scalar(select(AuditEvent).where(AuditEvent.org_id==pub.org_id,
                    AuditEvent.action=='publication.metadata',AuditEvent.details['publication_id'].as_string()==pub.id)
                    .order_by(AuditEvent.created_at.desc()).limit(1))
                if not previous_metadata or previous_metadata.details.get('metadata')!=state['_public_metadata']:
                    db.add(AuditEvent(org_id=pub.org_id,actor_id='worker',action='publication.metadata',
                        details={'publication_id':pub.id,'metadata':state['_public_metadata']}))
            db.flush(); campaign_status(db,campaign)
            db.add(AuditEvent(org_id=pub.org_id,actor_id='worker',action='publication.'+pub.status,details={'publication_id':pub.id,'provider':account.provider,'attempt':pub.attempts}))
            db.commit()
