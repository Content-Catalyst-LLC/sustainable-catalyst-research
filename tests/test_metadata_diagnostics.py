import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research-api'))
from app.metadata_diagnostics import inspect_metadata, revision_impact


def test_metadata_conflicts_and_missing_provenance_remain_proposals():
    a=dict(authority='library',source_id='one',source_version='1',source_sha256='a',doi='10.1000/one')
    b=dict(a,source_sha256='b')
    result=inspect_metadata([a,b])
    assert result['conflicts'][0]['field']=='source_sha256'
    assert all(r['status']=='proposed' for r in result['missing_metadata'])


def test_declared_impact_handles_cycles_and_does_not_infer_similarity():
    r=revision_impact('a',[('b','a'),('c','b'),('a','c'),('unrelated','other')])
    assert r['review_candidates']==['b','c']
    assert r['status']=='proposed'


def test_diagnostics_bounded():
    with pytest.raises(ValueError): inspect_metadata([{}]*501)
    with pytest.raises(ValueError): revision_impact('a',[('b','a')]*2001)
