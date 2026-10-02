"""Exercise upgrade/drift/downgrade/upgrade in an isolated PostgreSQL schema.

Run from root with DATABASE_URL set. The script removes only its random schema.
"""
import os
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    url=make_url(os.environ['DATABASE_URL'])
    if url.get_backend_name()!='postgresql': raise ValueError('This check requires PostgreSQL')
    schema='migration_check_'+uuid.uuid4().hex
    engine=create_engine(url)
    original=os.environ['DATABASE_URL']
    try:
        with engine.begin() as conn: conn.execute(text(f'CREATE SCHEMA {schema}'))
        os.environ['DATABASE_URL']=url.update_query_dict({'options':f'-csearch_path={schema}'}).render_as_string(hide_password=False)
        config=Config('alembic.ini')
        command.upgrade(config,'head')
        command.check(config)
        command.downgrade(config,'base')
        command.upgrade(config,'head')
        print('PostgreSQL migration round trip passed')
    finally:
        os.environ['DATABASE_URL']=original
        with engine.begin() as conn: conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        engine.dispose()


if __name__=='__main__': main()
