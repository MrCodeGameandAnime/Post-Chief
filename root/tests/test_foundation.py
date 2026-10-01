import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from postchief.config import Settings
from postchief.models import Base, Organization, Campaign, Publication, SocialAccount
from provider_contracts import ProviderError, ErrorReason


def test_configuration_rejects_missing_and_short_secrets():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite://", signing_key="short", encryption_key="bad")


def test_publications_enforce_one_destination_per_campaign():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="404 Builds")
        db.add(org)
        db.flush()
        campaign = Campaign(org_id=org.id, title="Release", body="Shipped")
        account = SocialAccount(org_id=org.id, provider="bluesky", remote_id="did:plc:test", name="404")
        db.add_all([campaign, account])
        db.flush()
        db.add_all([Publication(org_id=org.id, campaign_id=campaign.id, account_id=account.id), Publication(org_id=org.id, campaign_id=campaign.id, account_id=account.id)])
        with pytest.raises(IntegrityError):
            db.flush()


def test_error_contract_tells_client_whether_reconnect_is_required():
    error = ProviderError(ErrorReason.AUTH_EXPIRED, "Expired")
    assert error.to_dict() == {"reason": "AUTH_EXPIRED", "retryable": False, "action_required": "RECONNECT", "message": "Expired"}
