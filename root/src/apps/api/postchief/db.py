from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_engine(url: str):
    engine = create_engine(url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    return engine


def get_db(request):
    with request.app.state.sessions() as session:
        yield session
