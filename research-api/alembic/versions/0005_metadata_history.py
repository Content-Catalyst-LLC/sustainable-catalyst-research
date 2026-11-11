"""Freeze source versions and retain append-only relationship lifecycle history."""
from alembic import op
import sqlalchemy as sa
revision = '0005_metadata_history'
down_revision = '0004_public_rights'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_relation_history',
        sa.Column('sequence',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('relation_id',sa.String(36),nullable=False),
        sa.Column('operation',sa.String(8),nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('snapshot',sa.JSON(),nullable=False))
    if op.get_bind().dialect.name != 'postgresql': return
    op.execute('''CREATE FUNCTION research_freeze_source_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Research source versions cannot be deleted'; END IF;
      IF (to_jsonb(OLD) - ARRAY['visibility','rights_cleared','review_state']) IS DISTINCT FROM
         (to_jsonb(NEW) - ARRAY['visibility','rights_cleared','review_state']) THEN
        RAISE EXCEPTION 'Research source metadata requires a new version';
      END IF;
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER research_object_version BEFORE UPDATE OR DELETE
      ON research_metadata_objects FOR EACH ROW EXECUTE FUNCTION research_freeze_source_version()''')
    op.execute('''CREATE FUNCTION research_record_relation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        INSERT INTO research_relation_history(relation_id,operation,snapshot) VALUES(OLD.id,TG_OP,to_jsonb(OLD));
        RETURN OLD;
      END IF;
      INSERT INTO research_relation_history(relation_id,operation,snapshot) VALUES(NEW.id,TG_OP,to_jsonb(NEW));
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER research_relation_history AFTER INSERT OR UPDATE OR DELETE
      ON research_metadata_relations FOR EACH ROW EXECUTE FUNCTION research_record_relation()''')
    op.execute('''CREATE FUNCTION research_deny_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Research relationship history is append only'; END $$''')
    op.execute('''CREATE TRIGGER research_history_immutable BEFORE UPDATE OR DELETE
      ON research_relation_history FOR EACH ROW EXECUTE FUNCTION research_deny_history_change()''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER research_history_immutable ON research_relation_history')
        op.execute('DROP FUNCTION research_deny_history_change()')
        op.execute('DROP TRIGGER research_relation_history ON research_metadata_relations')
        op.execute('DROP FUNCTION research_record_relation()')
        op.execute('DROP TRIGGER research_object_version ON research_metadata_objects')
        op.execute('DROP FUNCTION research_freeze_source_version()')
    op.drop_table('research_relation_history')
