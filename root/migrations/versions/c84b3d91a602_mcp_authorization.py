"""Persistent MCP account linking and revocable OAuth grants."""
from alembic import op
import sqlalchemy as sa

revision = 'c84b3d91a602'
down_revision = 'b947d182b310'
branch_labels = None
depends_on = None


def identity():
    return [sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False)]


def token():
    return sa.Column('token_hash', sa.String(64), unique=True, nullable=False)


def expires():
    return sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False)


def upgrade():
    op.create_table('mcp_clients', sa.Column('id', sa.String(200), primary_key=True),
        sa.Column('redirect_uris', sa.JSON(), nullable=False))
    op.create_table('mcp_grants', *identity(),
        sa.Column('agent_key_id', sa.String(36), sa.ForeignKey('agent_keys.id'), nullable=False),
        sa.Column('client_id', sa.String(200), nullable=False),
        sa.Column('resource', sa.Text(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False))
    op.create_table('mcp_pending', *identity(), token(),
        sa.Column('parameters', sa.JSON(), nullable=False), expires(),
        sa.Column('consumed', sa.Boolean(), nullable=False))
    op.create_table('mcp_codes', *identity(), token(),
        sa.Column('grant_id', sa.String(36), sa.ForeignKey('mcp_grants.id'), nullable=False),
        sa.Column('challenge', sa.String(43), nullable=False),
        sa.Column('redirect_uri', sa.Text(), nullable=False), expires(),
        sa.Column('consumed', sa.Boolean(), nullable=False))
    op.create_table('mcp_families', *identity(),
        sa.Column('grant_id', sa.String(36), sa.ForeignKey('mcp_grants.id'), nullable=False),
        expires(), sa.Column('revoked', sa.Boolean(), nullable=False))
    for name in ['mcp_access', 'mcp_refresh']:
        columns = [*identity(), token(),
            sa.Column('family_id', sa.String(36), sa.ForeignKey('mcp_families.id'), nullable=False),
            sa.Column('scopes', sa.JSON(), nullable=False), expires()]
        if name == 'mcp_refresh': columns.append(sa.Column('consumed', sa.Boolean(), nullable=False))
        op.create_table(name, *columns)
    for name, column in [('mcp_grants', 'agent_key_id'), ('mcp_codes', 'grant_id'),
            ('mcp_families', 'grant_id'), ('mcp_access', 'family_id'), ('mcp_refresh', 'family_id'),
            ('mcp_pending', 'expires_at'), ('mcp_codes', 'expires_at'),
            ('mcp_families', 'expires_at'), ('mcp_access', 'expires_at'), ('mcp_refresh', 'expires_at')]:
        op.create_index('ix_' + name + '_' + column, name, [column])


def downgrade():
    for name in ['mcp_refresh', 'mcp_access', 'mcp_families', 'mcp_codes',
                 'mcp_pending', 'mcp_grants', 'mcp_clients']:
        op.drop_table(name)
