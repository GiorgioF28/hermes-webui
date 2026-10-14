"""The CRM dispatch uses the existing authentication and CSRF gates."""
import sys
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from api import routes


@pytest.fixture
def crm_stub(monkeypatch):
    calls = []

    def get(handler, parsed):
        calls.append(('GET', parsed.path))
        return parsed.path.endswith('/records')

    def post(handler, parsed, body):
        calls.append(('POST', parsed.path, body))
        return parsed.path.endswith('/drafts')

    monkeypatch.setitem(sys.modules, 'api.sicilia_crm', SimpleNamespace(handle_get=get, handle_post=post))
    monkeypatch.setattr(routes, '_authorize_request', lambda handler: True)
    monkeypatch.setattr(routes, '_check_csrf', lambda handler: True)
    monkeypatch.setattr(routes, 'read_body', lambda handler: {'transcript': 'Signora Rossi'})
    monkeypatch.setattr(routes.RequestDiagnostics, 'maybe_start', lambda *args, **kwargs: None)
    return calls


def test_crm_dispatch_get_and_post(crm_stub):
    handler = SimpleNamespace(headers={})
    assert routes.handle_get(handler, urlparse('/api/sicilia-crm/records')) is True
    assert routes.handle_post(handler, urlparse('/api/sicilia-crm/drafts')) is True
    assert crm_stub == [('GET', '/api/sicilia-crm/records'),
                        ('POST', '/api/sicilia-crm/drafts', {'transcript': 'Signora Rossi'})]


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_auth_rejection_precedes_crm_dispatch(monkeypatch, crm_stub, method):
    monkeypatch.setattr(routes, '_authorize_request', lambda handler: False)
    getattr(routes, 'handle_' + method.lower())(SimpleNamespace(headers={}),
                                             urlparse('/api/sicilia-crm/records'))
    assert crm_stub == []


def test_csrf_rejection_precedes_draft_provider(monkeypatch, crm_stub):
    responses = []
    monkeypatch.setattr(routes, '_check_csrf', lambda handler: False)
    monkeypatch.setattr(routes, 'j', lambda handler, data, status=200: responses.append(status))
    routes.handle_post(SimpleNamespace(headers={}), urlparse('/api/sicilia-crm/drafts'))
    assert crm_stub == []
    assert responses == [403]


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_unknown_crm_endpoint_has_explicit_404(monkeypatch, crm_stub, method):
    responses = []
    monkeypatch.setattr(routes, 'j', lambda handler, data, status=200: responses.append(status))
    assert getattr(routes, 'handle_' + method.lower())(
        SimpleNamespace(headers={}), urlparse('/api/sicilia-crm/unknown')) is True
    assert responses == [404]
