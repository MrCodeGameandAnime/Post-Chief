import pytest
import os
import uuid
from sqlalchemy import text
from sqlalchemy.engine import make_url
from postchief.db import make_engine
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from postchief.config import Settings
from postchief.main import create_app
from postchief.models import Base
from postchief.auth import bootstrap_owner


@pytest.fixture
def app(tmp_path):
    database_url=f"sqlite:///{tmp_path}/test.db"
    admin_engine=None
    schema=None
    if os.environ.get('POST_CHIEF_TEST_DATABASE_URL'):
        url=make_url(os.environ['POST_CHIEF_TEST_DATABASE_URL'])
        if url.get_backend_name()!='postgresql': raise ValueError('Integration test URL must use PostgreSQL')
        schema='test_'+uuid.uuid4().hex
        admin_engine=make_engine(url)
        with admin_engine.begin() as conn: conn.execute(text(f'CREATE SCHEMA {schema}'))
        database_url=url.update_query_dict({'options':f'-csearch_path={schema}'}).render_as_string(hide_password=False)
    settings = Settings(_env_file=None, database_url=database_url, signing_key="test-signing-key-" * 4, encryption_key=Fernet.generate_key().decode(), secure_cookies=False, bootstrap_email="owner@example.test", bootstrap_password="a-long-test-password", media_dir=str(tmp_path / "media"))
    application = create_app(settings)
    try:
        Base.metadata.create_all(application.state.engine)
        bootstrap_owner(application)
        yield application
    finally:
        application.state.engine.dispose()
        if admin_engine is not None:
            with admin_engine.begin() as conn: conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            admin_engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        response = c.post("/api/auth/login", json={"email": "owner@example.test", "password": "a-long-test-password"})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield c
