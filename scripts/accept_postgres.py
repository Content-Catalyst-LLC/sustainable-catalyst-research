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
from alembic.script import ScriptDirectory
sys.path.insert(0, os.getcwd())
from app import workspaces as ws
from app import object_registry as registry
from app import convergence
from app import editorial

url=(Path(os.environ['RESEARCH_DATABASE_URL_FILE']).read_text().strip()
     if os.getenv('RESEARCH_DATABASE_URL_FILE') else os.environ['RESEARCH_DATABASE_URL'])
engine=create_engine(url)
assert engine.dialect.name == 'postgresql'
with engine.connect() as connection:
    assert connection.scalar(text('select current_database()')) == 'research_staging'
    # The expected head comes from the migrations shipped beside the app, so a new migration cannot leave this stale.
    expected_head=ScriptDirectory(str(Path(ws.__file__).resolve().parents[1] / 'alembic')).get_current_head()
    assert connection.scalar(text('select version_num from alembic_version')) == expected_head, expected_head
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
    # This DB-level fixture bypasses the editor on purpose; return it to private so it does not
    # remain public state that the SC-ED-01 integrity check (rightly) reports.
    area.visibility='private'; entry.visibility='private'
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
h=lambda c: c*64
plain={'sourceRefs':[{'authority':'library','sourceId':'syn'}],'scientificClassification':'synthetic',
       'uncertainty':{'status':'not-quantified','reason':'fixture'},'artifactSha256':h('a'),
       'verification':{'status':'hash-verified'},'reviewStatus':'unreviewed'}
bundle=convergence.seal_bundle({'schema':convergence.BUNDLE_SCHEMA,
    'project':{'researchArea':'acceptance','projectKey':'conv-'+suffix,'entrySlug':'conv-'+suffix,
               'title':'Synthetic convergence','workspaceProjectRef':'workspace-project:syn-'+suffix},
    'objects':[{'localId':'ds','authority':'library','sourceId':'syn-'+suffix,'sourceVersion':'v1','kind':'dataset',
                'title':'Synthetic dataset','dataClassification':'synthetic','provenance':plain}],'relations':[]})
with Session(engine) as s:
    out=convergence.ingest_bundle(s,owner_scope='synthetic-a',bundle=bundle); s.commit()
    prov_object=out['objects']['ds']['id']
    assert convergence.ingest_bundle(s,owner_scope='synthetic-a',bundle=bundle)['objects']['ds']['id']==prov_object
for sql in ["UPDATE research_output_provenance SET record_sha256=:z WHERE object_id=:id",
            'DELETE FROM research_output_provenance WHERE object_id=:id']:
    try:
        with engine.begin() as c:
            c.execute(text(sql),{'id':prov_object,'z':h('0')})
        raise AssertionError('provenance mutation accepted')
    except DBAPIError:
        pass
print('PASS SC-MODEL-01 convergence ingest is idempotent and provenance is immutable')
actor=editorial.Actor('synthetic-a','owner','synthetic-a','*')
with Session(engine) as s:
    out=editorial.create_article(s,actor,area_slug='acceptance-editorial-'+suffix,project_key='ed-'+suffix,
        workspace_project_id='syn-ed-'+suffix,slug='ed-'+suffix,title='Synthetic editorial acceptance')
    rev=editorial.add_revision(s,actor,slug='ed-'+suffix,base_revision=0,title='Synthetic editorial acceptance',
        summary='',sections=[{'heading':'Overview','body':'Synthetic.'}],references=[])
    s.commit()
    chain=editorial.verify_chain(s)
    assert chain['valid'] and chain['events']>=3, chain
for sql in ["UPDATE research_editorial_events SET action='tamper' WHERE seq=(SELECT max(seq) FROM research_editorial_events)",
            'DELETE FROM research_editorial_events WHERE seq=(SELECT max(seq) FROM research_editorial_events)',
            "UPDATE research_editorial_revisions SET document_sha256=:z WHERE object_id=:o",
            "DELETE FROM research_area_stewards WHERE steward_scope='synthetic-a'"]:
    try:
        with engine.begin() as c:
            c.execute(text(sql),{'z':h('0'),'o':rev['objectId']})
        raise AssertionError('editorial record mutation accepted: '+sql[:40])
    except DBAPIError:
        pass
print('PASS SC-ED-01 editorial revisions, stewardship and audit chain are append-only')
from app import discovery_intake as intake
from app.discovery_adapters.models import Candidate, CandidateSource
import hashlib
hx=lambda t: hashlib.sha256(t.encode()).hexdigest()
src=CandidateSource(provider='crossref',provider_record_id='crossref:syn-'+suffix,retrieved_at='2026-11-13T00:00:00+00:00',
    query_sha256=hx('q'),request_sha256=hx('req'),response_sha256=hx('resp'),record_sha256=hx('rec-'+suffix))
cand=Candidate(candidate_id='cand-'+hx('c'+suffix)[:24],doi='10.0000/syn-'+suffix,title='Synthetic intake candidate',
    publication_year=2026,retracted=False,sources=(src,))
other_actor=editorial.Actor('synthetic-b','owner','synthetic-b','*')
with Session(engine) as s:
    saved=intake.save_candidate(s,actor,cand)
    assert intake.save_candidate(s,actor,cand)['id']==saved['id']
    intake.associate(s,actor,saved['id'],'ed-'+suffix)
    intake.propose_to_library(s,actor,saved['id'],[])
    s.commit()
    try:
        intake.dismiss(s,other_actor,saved['id'])
        raise AssertionError('cross-owner intake bypass')
    except LookupError:
        s.rollback()
    assert intake.verify_chain(s)['valid']
    assert intake.saved_view(s,s.get(intake.DiscoverySavedCandidate,saved['id']))['libraryProposal']['sent'] is False
for sql in ["UPDATE research_discovery_candidate_sources SET record_sha256=:z WHERE saved_id=:id",
            'DELETE FROM research_discovery_candidate_sources WHERE saved_id=:id',
            "UPDATE research_discovery_library_proposals SET sent=true WHERE saved_id=:id",
            "UPDATE research_discovery_intake_events SET action='tamper' WHERE saved_id=:id"]:
    try:
        with engine.begin() as c:
            c.execute(text(sql),{'id':saved['id'],'z':h('0')})
        raise AssertionError('intake record mutation accepted: '+sql[:40])
    except DBAPIError:
        pass
print('PASS RH-08D-04 intake: per-owner dedup, owner isolation; provenance, proposals and audit are append-only')
