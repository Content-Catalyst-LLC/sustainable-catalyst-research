"""SC-MODEL-01: immutable output provenance and Workspace publication approvals."""
from alembic import op
import sqlalchemy as sa
revision = '0006_convergence_provenance'
down_revision = '0005_metadata_history'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_output_provenance',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('object_id',sa.String(36),sa.ForeignKey('research_metadata_objects.id'),nullable=False,unique=True,index=True),
        sa.Column('schema',sa.String(80),nullable=False),
        sa.Column('record',sa.JSON(),nullable=False),
        sa.Column('record_sha256',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('research_publication_approvals',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('object_id',sa.String(36),sa.ForeignKey('research_metadata_objects.id'),nullable=False,index=True),
        sa.Column('dimension',sa.String(24),nullable=False),
        sa.Column('subject_ref',sa.String(200),nullable=False),
        sa.Column('workspace_approval_id',sa.String(120),nullable=False),
        sa.Column('workspace_decision_id',sa.String(120),nullable=False),
        sa.Column('actor_id',sa.String(160),nullable=False),
        sa.Column('actor_role',sa.String(80),nullable=False),
        sa.Column('record',sa.JSON(),nullable=False),
        sa.Column('record_sha256',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('object_id','dimension',name='uq_research_publication_approval'))
    if op.get_bind().dialect.name != 'postgresql': return
    op.execute('''CREATE FUNCTION research_deny_record_change() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Research provenance and approval records are immutable'; END $$''')
    for table in ('research_output_provenance', 'research_publication_approvals'):
        op.execute(f'''CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION research_deny_record_change()''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('research_output_provenance', 'research_publication_approvals'):
            op.execute(f'DROP TRIGGER {table}_immutable ON {table}')
        op.execute('DROP FUNCTION research_deny_record_change()')
    op.drop_table('research_publication_approvals')
    op.drop_table('research_output_provenance')
