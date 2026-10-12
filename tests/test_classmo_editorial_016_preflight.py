"""No-network guard assertions for CLASSMO 016 Provider route."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/classmo_editorial_016_preflight.py"
SPEC = importlib.util.spec_from_file_location("classmo_editorial_016_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)


def test_reject_all_write_endpoints_and_unreviewed_sql(monkeypatch):
    c = g.Client("a"*20, "synthetic")
    monkeypatch.setattr(g, "urlopen", lambda *_args, **_kwargs: pytest.fail("network"))
    for path, query in [("/database/query", g.LEDGER),
                        ("/database/migrations", "select 1"),
                        ("/database/query/read-only", "select dangerous_function()")]:
        with pytest.raises(g.Blocked, match="C016_MUTATING_OR_UNREVIEWED_ENDPOINT"):
            c.request(path, query)
    with pytest.raises(g.Blocked, match="C016_SQL_NOT_ALLOWED"):
        c.read("select pg_sleep(3)")

def test_credentials_required():
    with pytest.raises(g.Blocked, match="C016_CREDENTIAL_MISSING"):
        g.Client("a"*20, "")
    with pytest.raises(g.Blocked, match="C016_PROJECT_REF_INVALID"):
        g.Client("invalid", "synthetic")

def test_preflight_fails_if_016_already_present(monkeypatch):
    class Mock:
        ref = "a"*20
        def request(self, path, query=None):
            return {"id": self.ref}
        def read(self, sql):
            return ([{"allowed":1,"forbidden":0}] if sql == g.ENV else
                    [{"name":"015_course_configuration_ambiguity"}, {"name":"016_editorial_drafts"}]
                    if sql == g.LEDGER else [])
    monkeypatch.setattr(g,"verify",lambda *args:(["015_course_configuration_ambiguity"],"f"*64))
    with pytest.raises(g.Blocked, match="C016_LEDGER_NOT_READY"):
        g.execute(Mock(),Path("."),"a"*40,"b"*40)

def test_runner_routes_only_dedicated_readonly_script():
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/provider-gate-approval-core.yml")
    w = workflow.read_text()
    assert "classmo_editorial_016_readonly" in w
    assert "scripts/classmo_editorial_016_preflight.py" in w
    assert "PROVIDER_C016_BLOB" in w

@pytest.mark.parametrize(
    ("path", "query", "stage"),
    [
        ("", None, "project_metadata"),
        ("/database/query/read-only", g.ENV, "environment_readonly_sql"),
        ("/database/query/read-only", g.LEDGER, "migration_ledger_readonly_sql"),
        ("/database/query/read-only", g.DRAFT, "draft_catalog_readonly_sql"),
    ],
)
def test_http_403_identifies_only_safe_provider_stage(monkeypatch, path, query, stage):
    """Do not misclassify a project-access 403 as database-read permission failure."""
    secret = "synthetic-private-token"
    client = g.Client("a" * 20, secret)

    def denied(request, timeout):
        assert timeout == 45
        raise g.HTTPError(request.full_url, 403, "private response " + secret, None, None)

    monkeypatch.setattr(g, "urlopen", denied)
    with pytest.raises(g.Blocked) as caught:
        client.request(path, query)

    assert str(caught.value) == "C016_PROVIDER_HTTP_403 stage=" + stage
    assert secret not in str(caught.value)
    assert "private response" not in str(caught.value)


@pytest.mark.parametrize(
    ("path", "query", "stage"),
    [
        ("", None, "project_metadata"),
        ("/database/query/read-only", g.ENV, "environment_readonly_sql"),
    ],
)
def test_transport_failure_keeps_safe_stage_and_no_response_detail(monkeypatch, path, query, stage):
    def unavailable(_request, timeout):
        assert timeout == 45
        raise g.URLError("private proxy diagnostic")

    monkeypatch.setattr(g, "urlopen", unavailable)
    with pytest.raises(g.Blocked) as caught:
        g.Client("a" * 20, "synthetic").request(path, query)
    assert str(caught.value) == "C016_PROVIDER_UNVERIFIED stage=" + stage
    assert "private proxy" not in str(caught.value)

