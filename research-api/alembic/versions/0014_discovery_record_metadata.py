"""RH-08D-04 final review: each provider record keeps the candidate metadata it arrived with.

Adds ``research_discovery_candidate_sources.metadata`` so every record's metadata hash can be checked and later
disagreements (a retraction, a correction) reach the Library reviewer. Drops the save-time
``research_discovery_saved_candidates.possible_duplicate_of`` snapshot: possible duplicates are now computed in both
directions when read. Intake has never been enabled anywhere, so the tables are empty; the column is still added
with a default so the migration is safe on populated tables, and adding a column fires no row trigger.
"""
from alembic import op
import sqlalchemy as sa
revision = '0014_discovery_record_metadata'
down_revision = '0013_discovery_intake'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('research_discovery_candidate_sources') as batch:
        batch.add_column(sa.Column('metadata', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    with op.batch_alter_table('research_discovery_candidate_sources') as batch:
        batch.alter_column('metadata', server_default=None)
    with op.batch_alter_table('research_discovery_saved_candidates') as batch:
        batch.drop_column('possible_duplicate_of')


def downgrade():
    with op.batch_alter_table('research_discovery_saved_candidates') as batch:
        batch.add_column(sa.Column('possible_duplicate_of', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    with op.batch_alter_table('research_discovery_candidate_sources') as batch:
        batch.drop_column('metadata')
