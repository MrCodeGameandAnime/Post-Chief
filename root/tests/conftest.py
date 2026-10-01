import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from postchief.config import Settings
from postchief.main import create_app
from postchief.models import Base
from postchief.auth import bootstrap_owner


@pytest.fixture
def app(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/test.db", signing_key="test-signing-key-" * 4, encryption_key=Fernet.generate_key().decode(), secure_cookies=False, bootstrap_email="owner@example.test", bootstrap_password="a-long-test-password", media_dir=str(tmp_path / "media"))
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    bootstrap_owner(application)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        response = c.post("/api/auth/login", json={"email": "owner@example.test", "password": "a-long-test-password"})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield c
