import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research-api'))
from app.main import app
from app import workspaces as ws
from app.source_adapters import ADAPTERS, AuthorizedSourceContext


def test_storage_readiness_requires_migrations(monkeypatch):
    monkeypatch.setattr(ws,'_engine',None)
    client=TestClient(app)
    assert client.get('/api/research/v1/health').status_code==200
    r=client.get('/api/research/v1/ready')
    assert r.status_code==503
    assert r.headers['cache-control']=='no-store'


def test_storage_outage_response_has_no_credentials():
    def fail():
        raise OperationalError('private-sql',{},Exception('secret-password-and-path'))
        yield
    app.dependency_overrides[ws.get_session]=fail
    try:
        r=TestClient(app).get('/api/research/v1/objects')
        assert r.status_code==503
        assert 'secret' not in r.text and 'sql' not in r.text
    finally:
        app.dependency_overrides.clear()


def test_staging_cannot_bootstrap_schema(monkeypatch):
    monkeypatch.setenv('RESEARCH_ENV','staging')
    monkeypatch.setenv('RESEARCH_DEV_AUTO_CREATE_SCHEMA','1')
    with pytest.raises(RuntimeError): ws.bootstrap_dev_schema()


@pytest.mark.parametrize('authority',list(ADAPTERS))
def test_uncertified_adapters_never_fetch(authority):
    context=AuthorizedSourceContext(authority,'synthetic-owner','secret-ref','metadata:read','a'*64)
    with pytest.raises(PermissionError): ADAPTERS[authority].read_metadata(context,'source-id')
    wrong=AuthorizedSourceContext('library' if authority!='library' else 'lab','','','metadata:read','')
    with pytest.raises(PermissionError): ADAPTERS[authority].read_metadata(wrong,'source-id')
