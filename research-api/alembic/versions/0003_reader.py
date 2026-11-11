"""Least-privilege anonymous API role; password is provisioned outside migrations."""
from alembic import op
revision = '0003_reader'
down_revision = '0002_integrity'
branch_labels = None
depends_on = None

def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('GRANT USAGE ON SCHEMA public TO research_reader')
        op.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO research_reader')
        op.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO research_reader')

def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM research_reader')
        op.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM research_reader')
        op.execute('REVOKE USAGE ON SCHEMA public FROM research_reader')
