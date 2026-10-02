from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url, URL
from sqlalchemy.orm import sessionmaker
from fastapi import Request


def make_engine(url: str | URL):
    sqlite = make_url(url).get_backend_name() == 'sqlite'
    engine = create_engine(url, pool_pre_ping=True, connect_args={"check_same_thread": False} if sqlite else {})
    if sqlite:
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    return engine


def get_db(request: Request):
    with request.app.state.sessions() as session:
        yield session
