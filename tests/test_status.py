"""Status checks (console sidebar + check_setup): red without keys, no paid calls, versions + frozen tag."""
from __future__ import annotations

from ax15 import status


def test_missing_keys_are_red_and_never_call_out(monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(v, "")
    a, o, lu = status.anthropic_api(), status.openai_api(), status.luna()
    assert (a.ok, o.ok, lu.ok) == (False, False, False) and "missing" in a.detail


def test_langfuse_off_is_optional_not_red(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    assert status.langfuse().ok is None


def test_free_checks_use_model_lookup_not_messages(monkeypatch):
    """paid=False must not send a message (no tokens): only models.retrieve is called."""
    import anthropic
    calls = []

    class FakeModels:
        def retrieve(self, model):
            calls.append(("retrieve", model))

    class FakeMessages:
        def create(self, **kw):
            calls.append(("create", kw))

    class Fake:
        def __init__(self, **kw):
            self.models, self.messages = FakeModels(), FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    c = status.anthropic_api(paid=False)
    assert c.ok and calls and all(k == "retrieve" for k, _ in calls)


def test_can_run_needs_neon_and_anthropic():
    g, r = status.Check("x", True), status.Check("x", False)
    assert status.can_run({"neon": g, "anthropic": g})
    assert not status.can_run({"neon": r, "anthropic": g}) and not status.can_run({"neon": g, "anthropic": r})
    assert not status.can_run({})


def test_agent_versions_report_hashes_and_version():
    v = {c.name: c for c in status.agent_versions()}
    assert "AX-15 1.0" in v and "AX-15 2.0" in v and "prompt hash" in v["AX-15 2.0"].detail
    ver = v["AX-15 version"]
    assert ver.ok is not False and f"version {status.VERSION}" in ver.detail and "prompt/config hash" in ver.detail


def test_version_check_is_not_red_without_git(monkeypatch):
    """A zip download has no git and no tags: the version check still shows the constant and the hash, not red."""
    def no_git(*a):
        raise FileNotFoundError("git")
    monkeypatch.setattr(status, "_git", no_git)
    c = status.version_check()
    assert c.ok is True and c.detail.startswith(f"version {status.VERSION} · prompt/config hash ")
