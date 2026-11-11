"""Fail closed for legacy entries/releases with no separate rights approval."""
from alembic import op
import sqlalchemy as sa
revision = '0004_public_rights'
down_revision = '0003_reader'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('research_entries', sa.Column('rights_cleared', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('research_workspace_revisions', sa.Column('rights_cleared', sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade():
    op.drop_column('research_workspace_revisions', 'rights_cleared')
    op.drop_column('research_entries', 'rights_cleared')
