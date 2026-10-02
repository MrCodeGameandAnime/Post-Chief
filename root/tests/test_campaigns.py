from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from postchief.models import Organization, SocialAccount, User


def account(app, provider="bluesky", org_id=None):
    with app.state.sessions() as db:
        org_id = org_id or db.scalar(select(User.org_id))
        row = SocialAccount(org_id=org_id, provider=provider, remote_id=f"did:{provider}", name=provider)
        db.add(row)
        db.commit()
        return row.id


def test_campaign_round_trip_and_conflicting_edit(client, app):
    bluesky = account(app)
    linkedin = account(app, "linkedin")
    payload = {"title":"Release","body":"We shipped","account_ids":[bluesky, linkedin],"overrides":{"linkedin":{"body":"Professional update"}}}
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201
    campaign = response.json()
    assert len(campaign["publications"]) == 2
    assert {p["status"] for p in campaign["publications"]} == {"pending"}
    assert client.get(f'/api/campaigns/{campaign["id"]}').json()["overrides"]["linkedin"]["body"] == "Professional update"
    changed = client.patch(f'/api/campaigns/{campaign["id"]}', json={"revision":1,"title":"New release"})
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    assert client.patch(f'/api/campaigns/{campaign["id"]}', json={"revision":1,"title":"Stale edit"}).status_code == 409
    assert len(client.get("/api/planner").json()) == 1


def test_campaign_rejects_duplicate_accounts_and_naive_time(client, app):
    dest = account(app)
    payload = {"title":"Update","body":"Hello","account_ids":[dest,dest]}
    assert client.post("/api/campaigns", json=payload).status_code == 422
    payload["account_ids"] = [dest]
    payload["scheduled_at"] = "2026-10-05T08:00:00"
    assert client.post("/api/campaigns", json=payload).status_code == 422
    payload["scheduled_at"] = "2026-10-05T08:00:00-04:00"
    assert client.post("/api/campaigns", json=payload).json()["scheduled_at"] == "2026-10-05T12:00:00+00:00"


def test_campaign_cannot_use_another_organizations_account(client, app):
    own = account(app)
    assert client.post("/api/campaigns", json={"title":"Own draft","body":"Allowed","account_ids":[own]}).status_code == 201
    with app.state.sessions() as db:
        other = Organization(name="Other")
        db.add(other)
        db.commit()
        org_id = other.id
    foreign = account(app, org_id=org_id)
    assert client.post("/api/campaigns", json={"title":"Cross tenant","body":"No","account_ids":[foreign]}).status_code == 404


def test_asset_upload_download_and_invalid_image(client, app):
    from PIL import Image
    from io import BytesIO
    fixture = BytesIO()
    Image.new("RGB", (1,1), (255,0,0)).save(fixture, format="PNG")
    image = fixture.getvalue()
    response = client.post("/api/assets", files={"file":("pixel.png", image, "image/png")})
    assert response.status_code == 201
    asset = response.json()
    assert asset["mime_type"] == "image/png"
    assert asset["details"]["width"] == 1
    assert client.get(f'/api/assets/{asset["id"]}/file').content == image
    assert client.post("/api/assets", files={"file":("fake.png",b"not an image","image/png")}).status_code == 422
    dest = account(app)
    campaign = client.post("/api/campaigns", json={"title":"Picture","body":"Pixel","account_ids":[dest],"asset_ids":[asset["id"]]}).json()
    assert campaign["asset_ids"] == [asset["id"]]
    assert client.delete(f'/api/assets/{asset["id"]}').status_code == 409
    # A concurrent/direct storage operation must also preserve referenced assets.
    import pytest
    from sqlalchemy.exc import IntegrityError
    from postchief.models import Asset
    with app.state.sessions() as db:
        db.delete(db.get(Asset,asset["id"]))
        with pytest.raises(IntegrityError):
            db.flush()


def test_published_campaign_cannot_be_edited_or_deleted(client, app):
    from postchief.models import Publication
    dest = account(app)
    campaign = client.post("/api/campaigns",json={"title":"Published","body":"Original","account_ids":[dest]}).json()
    with app.state.sessions() as db:
        row = db.get(Publication,campaign["publications"][0]["id"])
        row.status = "published"
        row.provider_id = "provider-record"
        db.commit()
    assert client.patch(f'/api/campaigns/{campaign["id"]}',json={"revision":1,"body":"Changed history"}).status_code == 409
    assert client.delete(f'/api/campaigns/{campaign["id"]}').status_code == 409


def test_asset_download_is_scoped_and_active_svg_is_rejected(client, app):
    assert client.post("/api/assets",files={"file":("active.svg",b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',"image/svg+xml")}).status_code == 422
    assert client.get("/api/assets/nonexistent/file").status_code == 404
    assert client.post("/api/campaigns",json={"title":"Blank","body":" ","account_ids":[account(app)]}).status_code == 422
