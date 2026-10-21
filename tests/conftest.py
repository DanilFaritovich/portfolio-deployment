"""Shared safety isolation for offline Stage 07 host-operation tests."""
import pytest


@pytest.fixture(autouse=True)
def isolate_e2e_host_home(request, monkeypatch, tmp_path):
    """Keep every E2E test's host state and Snap-facing home inside pytest temp."""
    if request.node.path.name.startswith(("test_local_e2e.py", "test_e2e_")):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("E2E_STATE_DIR", raising=False)
        monkeypatch.delenv("E2E_FIXTURE_DIR", raising=False)
