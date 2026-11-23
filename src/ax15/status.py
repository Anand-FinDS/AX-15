"""Service and version checks shared by scripts/check_setup.py and the console status sidebar.

Each check returns a Check (ok True / False, or None = optional and not configured). With paid=False (the
console) no tokens are spent: providers are checked with an authenticated model lookup (key present + auth
+ the configured model exists), Langfuse with auth_check, Neon with SELECT 1 (which also wakes a
scaled-to-zero endpoint). paid=True (check_setup) sends the old one-line pings. Never prints secret values.
"""
from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ax15.config import base_llm, embedding_cfg, fallback_llm, models
from ax15.settings import ROOT, dataset_dir

VERSION = "2.0.2"                     # 1.0 = frozen baseline; 2.0 = current, incl. the safety rules
FROZEN_TAG = f"v{VERSION}"           # optional: only adds a note when the git tag exists
# Everything an agent run depends on (prompts, rules, graph, tools, retrieval, guard, model config).
FROZEN_PATHS = ("src/ax15/agents", "src/ax15/tools", "src/ax15/trust", "src/ax15/l1_knowledge", "prompts", "config")


@dataclass
class Check:
    name: str
    ok: bool | None            # None = optional, not configured
    detail: str = ""
    ms: int | None = None

    @property
    def icon(self) -> str:
        return {True: "🟢", False: "🔴", None: "⚪"}[self.ok]


def _has(var: str) -> bool:
    return bool(os.getenv(var, "").strip())


def _short(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}"


def _timed(name: str, fn) -> Check:
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except BaseException as e:  # noqa: BLE001 - SystemExit from db.connect included
        ok, detail = False, _short(e)
    return Check(name, ok, detail, int((time.perf_counter() - t0) * 1000))


def neon() -> Check:
    def f():
        from ax15 import db
        if not db.configured():
            return False, "DATABASE_URL not set"
        conn, var = db.connect(autocommit=True)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return True, f"connected via {var}"
    c = _timed("Neon Postgres", f)
    if c.ok:
        c.detail += f" · {c.ms} ms"
    return c


def postgres(var: str) -> Check:
    """check_setup: one URL, server version + pgvector availability (read-only)."""
    def f():
        if not _has(var):
            return (False if var == "DATABASE_URL" else None), f"{var} not set"
        import psycopg
        with psycopg.connect(os.environ[var], connect_timeout=15) as conn:
            ver = conn.execute("select current_setting('server_version')").fetchone()[0]
            row = conn.execute("select default_version from pg_available_extensions where name = 'vector'").fetchone()
        return bool(row), f"PG {ver}; " + (f"pgvector available v{row[0]}" if row else "pgvector NOT available")
    return _timed(f"Postgres {var}", f)


def anthropic_api(paid: bool = False) -> Check:
    model = base_llm()["model"]

    def f():
        if not _has("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY missing (required)"
        import anthropic
        client = anthropic.Anthropic(timeout=15, max_retries=0)
        if paid:
            r = client.messages.create(model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with OK"}])
            return True, f"{model}: {r.content[0].text.strip()[:40]}"
        client.models.retrieve(model)
        return True, f"key ok · {model} available"
    return _timed("Anthropic", f)


def openai_api(paid: bool = False) -> Check:
    emb = embedding_cfg()

    def f():
        if not _has("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY missing (required: embeddings)"
        from openai import OpenAI
        client = OpenAI(timeout=15, max_retries=0)
        if paid:
            e = client.embeddings.create(model=emb["model"], input="ping", dimensions=emb["dimensions"])
            return True, f"{emb['model']}: dims={len(e.data[0].embedding)}"
        client.models.retrieve(emb["model"])
        return True, f"key ok · {emb['model']} available"
    return _timed("OpenAI (embeddings)", f)


def luna(paid: bool = False) -> Check:
    model = fallback_llm()["model"]

    def f():
        if not _has("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY missing"
        from openai import OpenAI
        client = OpenAI(timeout=15, max_retries=0)
        if paid:
            r = client.responses.create(model=model, input="Reply with OK")
            return True, f"{model}: {(r.output_text or '').strip()[:40]}"
        client.models.retrieve(model)
        return True, f"key ok · {model} available"
    return _timed("Luna fallback", f)


def groq(paid: bool = True) -> Check:
    cfg = models()["llm"]["open_source"]

    def f():
        if not _has("GROQ_API_KEY"):
            return None, "GROQ_API_KEY not set (optional)"
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=cfg["base_url"], timeout=15, max_retries=0)
        if paid:
            r = client.chat.completions.create(model=cfg["model"], messages=[{"role": "user", "content": "Reply with OK"}],
                                               max_tokens=512)
            return True, f"{cfg['model']}: {(r.choices[0].message.content or '').strip()[:40]}"
        ids = {m.id for m in client.models.list()}             # retrieve() would URL-encode the "/" in the id
        return cfg["model"] in ids, f"key ok · {cfg['model']} " + ("available" if cfg["model"] in ids else "not listed")
    return _timed("Groq (optional)", f)


def langfuse() -> Check:
    def f():
        if not all(_has(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")):
            return None, "keys not set (local JSON traces only)"
        if os.getenv("AX15_LANGFUSE", "1") == "0":
            return None, "disabled by AX15_LANGFUSE=0 (local JSON traces only)"
        from langfuse import Langfuse
        ok = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                      host=os.environ["LANGFUSE_HOST"], timeout=10).auth_check()
        return bool(ok), "auth ok" if ok else "auth failed"
    return _timed("Langfuse", f)


def dataset() -> Check:
    ds = dataset_dir()
    expected = ["data/stores.csv", "data/assets.csv", "kb/runbooks", "tickets/train", "labels/train_labels.json"]
    missing = [p for p in expected if not (ds / p).exists()]
    return Check("Dataset", not missing, f"missing: {missing}" if missing else str(ds))


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10)


def agent_versions() -> list[Check]:
    """1.0 and 2.0 prompt hashes, and whether the agent code still matches the current 2.0 tag (FROZEN_TAG)."""
    out = []
    try:
        from ax15.agents import v1_single, v2_graph
        out += [Check("AX-15 1.0", True, f"single agent · prompt hash {v1_single.prompt_hash()}"),
                Check("AX-15 2.0", True, f"LangGraph workflow · prompt hash {v2_graph.prompt_hash()}")]
    except Exception as e:  # noqa: BLE001
        out.append(Check("AX-15 agents", False, _short(e)))
    out.append(version_check())
    return out


def config_hash() -> str:
    """sha256 over prompts/ and config/ (what the agents read besides code): shows which configuration is live."""
    import hashlib
    h = hashlib.sha256()
    for d in ("prompts", "config"):
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file():
                h.update(p.relative_to(ROOT).as_posix().encode())
                h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def version_check() -> Check:
    """Version from the VERSION constant + the prompt/config hash. Never red: git and tags are optional
    (a zip download has neither). With the tag present, it also says whether the agent code still matches it."""
    detail = f"version {VERSION} · prompt/config hash {config_hash()}"
    ok: bool | None = True
    try:
        tag = _git("rev-parse", "--short", "--verify", "-q", f"{FROZEN_TAG}^{{commit}}")
        if tag.returncode == 0:
            same = _git("diff", "--quiet", FROZEN_TAG, "--", *FROZEN_PATHS).returncode == 0
            detail += f" · git tag {FROZEN_TAG}: " + ("matches" if same else "changed since the tag")
            ok = True if same else None
    except Exception:  # noqa: BLE001 - no git: the constant and the hash are enough
        pass
    return Check("AX-15 version", ok, detail)


def console_checks() -> dict[str, Check]:
    """Everything the console sidebar shows. No paid calls. Runs the network checks in parallel."""
    fns = {"neon": neon, "anthropic": anthropic_api, "openai": openai_api, "luna": luna, "langfuse": langfuse}
    with ThreadPoolExecutor(len(fns)) as pool:
        futs = {k: pool.submit(f) for k, f in fns.items()}
        res = {k: f.result() for k, f in futs.items()}
    for i, c in enumerate(agent_versions()):
        res[f"version_{i}"] = c
    return res


def can_run(checks: dict[str, Check]) -> bool:
    """Agent runs need the database and the base model."""
    return bool(checks.get("neon") and checks["neon"].ok and checks.get("anthropic") and checks["anthropic"].ok)
