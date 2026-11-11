"""Synthetic acceptance against ONLY the dedicated migrated Research candidate DB.
Run within candidate image/network with RESEARCH_DATABASE_URL_FILE. No real accounts.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
sys.path.insert(0, os.getcwd())
from app import workspaces as ws
from app import object_registry as registry

url=(Path(os.environ['RESEARCH_DATABASE_URL_FILE']).read_text().strip()
     if os.getenv('RESEARCH_DATABASE_URL_FILE') else os.environ['RESEARCH_DATABASE_URL'])
engine=create_engine(url)
assert engine.dialect.name == 'postgresql'
with engine.connect() as connection:
    assert connection.scalar(text('select current_database()')) == 'research_staging'
    assert connection.scalar(text('select version_num from alembic_version')) == '0005_metadata_history'
print('PASS migrated dedicated PostgreSQL')
suffix=uuid4().hex[:10]
with Session(engine) as s:
    w=ws.create_entry_workspace(s,area_slug='acceptance',project_key='test-'+suffix,
        owner_scope='synthetic-a',entry_slug='test-'+suffix,title='Synthetic staging acceptance')
    other=ws.create_entry_workspace(s,area_slug='acceptance',project_key='other-'+suffix,
        owner_scope='synthetic-b',entry_slug='other-'+suffix,title='Synthetic second owner')
    o=registry.register_metadata_object(s,workspace=w,owner_scope='synthetic-a',
        authority='research',source_id='synthetic',source_version='1',kind='dataset',title='Private sentinel')
    try:
        registry.register_metadata_object(s,workspace=w,owner_scope='synthetic-b',authority='research',
            source_id='forbidden',source_version='1',kind='dataset',title='Forbidden')
        raise AssertionError('owner bypass')
    except PermissionError:
        pass
    assert ws.approved_workspace_view(s,'test-'+suffix) is None
    assert not s.execute(registry.public_query().where(registry.ResearchObject.id==o.id)).all()
    r=ws.add_private_revision(s,workspace=w,change_summary='synthetic private',content_ref='private:sentinel',content_sha256='a'*64)
    r.released_at=datetime.now(timezone.utc)
    s.commit()
    ids=(w.id,other.id,o.id,r.id)
print('PASS cross-owner registration denial and private projections')
with Session(engine) as s:
    assert s.get(ws.EntryWorkspace,ids[0]) is not None
    assert s.get(registry.ResearchObject,ids[2]).source_version=='1'
print('PASS persistence across connections')
for sql in ["UPDATE research_workspace_revisions SET change_summary='tamper' WHERE id=:id",
            'DELETE FROM research_workspace_revisions WHERE id=:id']:
    try:
        with engine.begin() as c:
            c.execute(text(sql),{'id':ids[3]})
        raise AssertionError('immutable release bypass')
    except DBAPIError:
        pass
print('PASS database rejects UPDATE and DELETE of released revisions')
with Session(engine) as s:
    foreign=registry.register_metadata_object(s,workspace=s.get(ws.EntryWorkspace,ids[1]),owner_scope='synthetic-b',
        authority='research',source_id='foreign',source_version='1',kind='dataset',title='Foreign')
    s.commit(); foreign_id=foreign.id
try:
    with engine.begin() as c:
        c.execute(text('''INSERT INTO research_metadata_relations
          (id,workspace_id,subject_id,object_id,predicate,private_basis,public_note,review_state,visibility,evidence_assertion)
          VALUES (:id,:w,:a,:b,'related-to','','','proposed','private',false)'''),
          {'id':str(uuid4()),'w':ids[0],'a':ids[2],'b':foreign_id})
    raise AssertionError('cross-owner SQL bypass')
except DBAPIError:
    pass
print('PASS database rejects cross-owner relationship')
print('PASS synthetic-only acceptance; real-account acceptance NOT PERFORMED')
with Session(engine) as s:
    w=s.get(ws.EntryWorkspace,ids[0]); entry=s.get(ws.ResearchEntry,w.entry_id)
    project=s.get(ws.ResearchProject,entry.project_id); area=s.get(ws.ResearchArea,project.area_id)
    area.visibility=ws.APPROVED; entry.visibility=ws.APPROVED; entry.rights_cleared=True
    obj=s.get(registry.ResearchObject,ids[2]); obj.visibility=ws.APPROVED
    obj.review_state=ws.APPROVED; obj.rights_cleared=True
    s.flush()
    assert s.execute(registry.public_query().where(registry.ResearchObject.id==obj.id)).first()
    entry.rights_cleared=False; s.flush()
    assert not s.execute(registry.public_query().where(registry.ResearchObject.id==obj.id)).first()
    assert ws.approved_workspace_view(s,entry.slug) is None
    s.commit()
print('PASS entry rights revocation hides workspace, catalog and objects')
try:
    with engine.begin() as c:
        c.execute(text("UPDATE research_metadata_objects SET source_version='tamper' WHERE id=:id"),{'id':ids[2]})
    raise AssertionError('source version mutation accepted')
except DBAPIError:
    pass
print('PASS source version immutability')
with Session(engine) as s:
    w=s.get(ws.EntryWorkspace,ids[0])
    one=s.get(registry.ResearchObject,ids[2])
    two=registry.register_metadata_object(s,workspace=w,owner_scope='synthetic-a',authority='research',
        source_id='second',source_version='1',kind='analysis',title='Synthetic dependency')
    relation=registry.propose_relation(s,workspace=w,owner_scope='synthetic-a',subject=one,target=two,
        predicate='declared-dependency',private_basis='synthetic fixture')
    s.commit(); relation_id=relation.id
    relation.review_state='reviewed'; s.commit()
with engine.connect() as c:
    assert c.scalar(text('select count(*) from research_relation_history where relation_id=:id'),{'id':relation_id})==2
try:
    with engine.begin() as c:
        c.execute(text('delete from research_relation_history where relation_id=:id'),{'id':relation_id})
    raise AssertionError('history mutation accepted')
except DBAPIError:
    pass
print('PASS append-only relationship history')
