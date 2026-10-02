"""Owner-reported posts are audit records, never worker destinations."""
from sqlalchemy import select
from postchief.models import AuditEvent


def external_posts(db, campaign):
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.org_id == campaign.org_id,
        AuditEvent.action == 'campaign.external_post',
        AuditEvent.details['campaign_id'].as_string() == campaign.id,
    ).order_by(AuditEvent.created_at, AuditEvent.id))
    return [{'id': event.id, **event.details} for event in events]
