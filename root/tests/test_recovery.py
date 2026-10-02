"""Synthetic PostgreSQL dump/media restore rehearsal; never starts dispatch.

Opt in with POST_CHIEF_RECOVERY_DATABASE_URL and POST_CHIEF_RECOVERY_CONTAINER.
The container must be the PostgreSQL server addressed by that URL. Only two
new random databases are created, inspected, and removed; no existing database
is backed up or restored. Docker provides matching pg_dump/pg_restore clients.
"""
import io
import os
import shutil
import subprocess
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from postchief.config import Settings
from postchief.main import create_app
from postchief.models import (
    AnalyticsSnapshot, Base, Campaign, Publication, SocialAccount, User, now,
)
from postchief.publishing.media import media_for_campaign
from postchief.vault import Vault


@pytest.fixture
def recovery_databases(monkeypatch):
    raw_url = os.environ.get("POST_CHIEF_RECOVERY_DATABASE_URL")
    container = os.environ.get("POST_CHIEF_RECOVERY_CONTAINER")
    if not raw_url or not container:
        pytest.skip("Requires an explicitly selected disposable PostgreSQL server")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Recovery rehearsal requires PostgreSQL")
    # Disable dotenv and inherited production settings, including OAuth secrets.
    settings_names = {name.casefold() for name in Settings.model_fields}
    for name in tuple(os.environ):
        if name.casefold() in settings_names:
            monkeypatch.delenv(name)
    names = ["recovery_" + uuid4().hex for _ in range(2)]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    created = []
    try:
        with admin.connect() as connection:
            for name in names:
                # Names are generated here, never supplied by configuration.
                connection.execute(text(f'CREATE DATABASE "{name}"'))
                created.append(name)
        yield url, container, names
    finally:
        with admin.connect() as connection:
            for name in reversed(created):
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def migrate(monkeypatch, url, *, check=False):
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    config = Config("alembic.ini")
    if check:
        command.check(config)
    else:
        command.upgrade(config, "head")


def pg_tool(container, tool, user, database, *, data=None):
    args = ["docker", "exec"]
    if data is not None:
        args.append("-i")
    args += [container, tool, "--username", user, "--dbname", database,
             "--no-owner", "--no-acl"]
    args += ["--format=custom"] if tool == "pg_dump" else ["--exit-on-error"]
    result = subprocess.run(args, input=data, capture_output=True, timeout=60)
    # Do not include command output or configuration in assertion messages.
    assert result.returncode == 0, f"{tool} failed during synthetic rehearsal"
    return result.stdout


def counts(app):
    with app.state.engine.connect() as connection:
        return {
            table.name: connection.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        }


def test_migration_command_disposes_its_connection_pool(monkeypatch, tmp_path):
    import postchief.db

    engines = []
    original = postchief.db.make_engine

    def tracked_engine(url):
        engine = original(url)
        engines.append(engine)
        return engine

    monkeypatch.setattr(postchief.db, "make_engine", tracked_engine)
    try:
        migrate(monkeypatch, make_url(f"sqlite:///{tmp_path / 'migration.db'}"))
        assert engines
        assert all(engine.pool.checkedout() == 0 for engine in engines)
        assert all(engine.pool.checkedin() == 0 for engine in engines)
    finally:
        for engine in engines:
            engine.dispose()


def test_postgresql_dump_and_media_restore_preserve_operational_state(
    recovery_databases, monkeypatch, tmp_path,
):
    url, container, (source_name, restored_name) = recovery_databases
    source_url, restored_url = url.set(database=source_name), url.set(database=restored_name)
    migrate(monkeypatch, source_url)
    signing_key = "recovery-fixture-signing-key-" * 3
    encryption_key = Fernet.generate_key().decode()
    source_media = tmp_path / "source-media"
    restored_media = tmp_path / "restored-media"
    settings = Settings(
        _env_file=None, database_url=source_url.render_as_string(hide_password=False),
        signing_key=signing_key, encryption_key=encryption_key, secure_cookies=False,
        bootstrap_email="recovery@example.test", bootstrap_password="recovery-fixture-password",
        media_dir=str(source_media), web_dir=str(tmp_path / "no-dashboard"),
    )
    source = create_app(settings)
    restored = None
    try:
        with TestClient(source) as client:
            login = client.post("/api/auth/login", json={
                "email": settings.bootstrap_email, "password": "recovery-fixture-password",
            })
            assert login.status_code == 200
            old_session = client.cookies.get("pc_session")
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            image = io.BytesIO()
            Image.new("RGB", (8, 8), "green").save(image, "JPEG")
            content = image.getvalue()
            upload = client.post("/api/assets", files={"file": ("fixture.jpg", content, "image/jpeg")})
            assert upload.status_code == 201
            asset_id = upload.json()["id"]
            vault = Vault(encryption_key)
            credentials = {"access_token": "synthetic-token-not-valid-at-any-provider"}
            checkpoint = {"container_id": "synthetic-container", "publish_intent": True}
            with source.state.sessions() as db:
                owner = db.scalar(select(User))
                account = SocialAccount(org_id=owner.org_id, provider="instagram",
                    remote_id="synthetic-account", name="Recovery fixture",
                    credentials=vault.encrypt(credentials))
                db.add(account)
                db.flush()
                campaign = Campaign(org_id=owner.org_id, title="Recovery fixture",
                    body="Never publish this fixture", asset_ids=[asset_id],
                    status="scheduled", scheduled_at=now() + timedelta(days=1))
                db.add(campaign)
                db.flush()
                publication = Publication(org_id=owner.org_id, campaign_id=campaign.id,
                    account_id=account.id, status="processing", attempts=2,
                    provider_state={"sealed": vault.encrypt(checkpoint)})
                db.add(publication)
                db.flush()
                db.add(AnalyticsSnapshot(org_id=owner.org_id, publication_id=publication.id,
                    metrics={"available": True, "normalized": {"likes": 0}}))
                db.commit()
                account_id, publication_id, campaign_id = account.id, publication.id, campaign.id
                _, media = media_for_campaign(db, campaign, account, settings)
                signed_path = media[0].url.removeprefix(settings.public_url)
            before_counts = counts(source)
            with source.state.engine.connect() as connection:
                migration_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
            # Capture binary dump directly, never through PowerShell text pipes.
            dump = pg_tool(container, "pg_dump", url.username, source_name)
            assert dump.startswith(b"PGDMP")
            archive = shutil.make_archive(str(tmp_path / "media"), "zip", source_media)

        pg_tool(container, "pg_restore", url.username, restored_name, data=dump)
        shutil.unpack_archive(archive, restored_media)
        restored_settings = settings.model_copy(update={
            "database_url": restored_url.render_as_string(hide_password=False),
            "media_dir": str(restored_media), "bootstrap_email": "",
        })
        restored = create_app(restored_settings)
        assert counts(restored) == before_counts
        with restored.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == migration_head
        migrate(monkeypatch, restored_url, check=True)
        with restored.state.sessions() as db:
            account = db.get(SocialAccount, account_id)
            publication = db.get(Publication, publication_id)
            assert vault.decrypt(account.credentials) == credentials
            assert vault.decrypt(publication.provider_state["sealed"]) == checkpoint
            assert publication.status == "processing" and publication.attempts == 2
            assert db.get(Campaign, campaign_id).status == "scheduled"
            assert db.scalar(select(AnalyticsSnapshot)).metrics["normalized"]["likes"] == 0
            wrong_vault = Vault(Fernet.generate_key().decode())
            with pytest.raises(InvalidToken):
                wrong_vault.decrypt(account.credentials)
            with pytest.raises(InvalidToken):
                wrong_vault.decrypt(publication.provider_state["sealed"])
        with TestClient(restored) as client:
            assert client.get(f"/api/assets/{asset_id}/file").status_code == 401
            client.cookies.set("pc_session", old_session)
            assert client.get("/api/auth/me").status_code == 200
            assert client.get(f"/api/assets/{asset_id}/file").content == content
            assert client.get(signed_path).content == content
            restored_file = next(restored_media.iterdir())
            restored_file.unlink()
            assert client.get(f"/api/assets/{asset_id}/file").status_code == 404
            restored_file.write_bytes(content)
            restored.state.settings = restored_settings.model_copy(update={
                "signing_key": SecretStr("wrong-key-" * 8),
            })
            assert client.get("/api/auth/me").status_code == 401
            assert client.get(signed_path).status_code == 403
            restored.state.settings = restored_settings
            client.cookies.clear()
            assert client.post("/api/auth/login", json={
                "email": settings.bootstrap_email, "password": "recovery-fixture-password",
            }).status_code == 200
    finally:
        source.state.engine.dispose()
        if restored is not None:
            restored.state.engine.dispose()
