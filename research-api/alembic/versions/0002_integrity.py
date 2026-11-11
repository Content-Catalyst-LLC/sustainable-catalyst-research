"""PostgreSQL enforces immutable releases and owner-consistent relations."""
from alembic import op
revision = '0002_integrity'
down_revision = '0001_research'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('''CREATE FUNCTION research_protect_release() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.released_at IS NOT NULL THEN
        RAISE EXCEPTION 'Released research revisions are immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER research_revision_immutable BEFORE UPDATE OR DELETE
      ON research_workspace_revisions FOR EACH ROW EXECUTE FUNCTION research_protect_release()''')
    op.execute('''CREATE FUNCTION research_relation_owner_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE owner_count integer; endpoint_count integer;
    BEGIN
      SELECT count(DISTINCT p.owner_scope), count(DISTINCT w.id)
      INTO owner_count, endpoint_count
      FROM research_entry_workspaces w
      JOIN research_entries e ON e.id=w.entry_id
      JOIN research_projects p ON p.id=e.project_id
      WHERE w.id IN (NEW.workspace_id,
        (SELECT workspace_id FROM research_metadata_objects WHERE id=NEW.subject_id),
        (SELECT workspace_id FROM research_metadata_objects WHERE id=NEW.object_id));
      IF owner_count <> 1 THEN RAISE EXCEPTION 'Cross-owner research relation forbidden'; END IF;
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER research_relation_owner BEFORE INSERT OR UPDATE
      ON research_metadata_relations FOR EACH ROW EXECUTE FUNCTION research_relation_owner_guard()''')


def downgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('DROP TRIGGER research_relation_owner ON research_metadata_relations')
    op.execute('DROP FUNCTION research_relation_owner_guard()')
    op.execute('DROP TRIGGER research_revision_immutable ON research_workspace_revisions')
    op.execute('DROP FUNCTION research_protect_release()')
