"""Analytics collection leases and refresh deadlines."""
from alembic import op
import sqlalchemy as sa

revision='b947d182b310'
down_revision='e31efbe1cd6c'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('publications',sa.Column('analytics_started_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('publications',sa.Column('analytics_next_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('publications',sa.Column('analytics_error',sa.JSON(),nullable=True))
    op.create_index('ix_publications_analytics_next_at','publications',['analytics_next_at'])

def downgrade():
    op.drop_index('ix_publications_analytics_next_at',table_name='publications')
    op.drop_column('publications','analytics_error')
    op.drop_column('publications','analytics_next_at')
    op.drop_column('publications','analytics_started_at')
