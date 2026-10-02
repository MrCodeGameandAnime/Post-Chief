import os
from alembic import context
from postchief.db import make_engine
from postchief.models import Base

url = os.environ.get("DATABASE_URL", "postgresql+psycopg://postchief:postchief@localhost:5432/postchief")
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
