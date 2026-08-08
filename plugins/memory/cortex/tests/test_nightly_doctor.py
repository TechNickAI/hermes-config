from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nightly_doctor.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nightly_doctor", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class FakeEmbedder:
    model = "fake-embedding"
    dimensions = 4

    def __init__(self, *args, **kwargs):
        pass

    def health(self):
        return True

    def embed(self, texts):
        out = []
        for text in texts:
            seed = sum(text.encode("utf-8")) or 1
            out.append([float((seed + i) % 11 + 1) for i in range(self.dimensions)])
        return out


def make_store(tmp_path: Path, pages=2):
    mod = load_module()
    st = mod.CortexStore(tmp_path / "cortex", embedder=FakeEmbedder())
    st.write_page("topics", "memory-operations", "Memory operations database repair runbook.")
    st.write_page("decisions", "memory-source", "Cortex memory is the durable knowledge source.")
    for i in range(pages - 2):
        st.write_page("topics", f"memory-extra-{i}", f"Memory extra note {i} about the knowledge base.")
    st.backfill_embeddings(force=True)
    return mod, st


def corrupt_unrelated_page(db: Path, rel_path: str) -> None:
    """Orphan one FTS row so integrity-check fails but a MATCH probe still works."""
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER pages_ad")
    conn.execute("DELETE FROM pages WHERE rel_path=?", (rel_path,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------- happy path


def test_healthy_store_passes_without_repairs(tmp_path, monkeypatch):
    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["ok"] is True
    assert result["repairs"] == []
    assert result["fts_after"]["ok"] is True
    assert result["embeddings_after"]["embedded"] == result["embeddings_after"]["pages"] == 2
    assert result["retrieval"]["ok"] is True


def test_absent_query_term_is_not_treated_as_corruption(tmp_path, monkeypatch):
    """Zero matches means the word is absent, not that the index is broken.

    Conflating them made a healthy store get rebuilt every night and still
    report failure.
    """
    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "zzzznonexistentterm", repair=True)

    assert result["fts_before"]["corrupt"] is False
    assert result["fts_before"]["rows"] == 0
    assert result["repairs"] == []
    assert result["ok"] is True


# ------------------------------------------------------- corruption + repair


def test_corruption_outside_the_probe_query_is_still_detected(tmp_path, monkeypatch):
    """A MATCH probe alone misses damage in pages the query does not hit."""
    mod, st = make_store(tmp_path)
    st.write_page("topics", "widget-catalog", "Widget catalog of sprockets and flanges.")
    st.backfill_embeddings(force=True)
    st.close()
    db = tmp_path / "cortex/.plugin.db"
    corrupt_unrelated_page(db, "topics/widget-catalog.md")

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT pages.rel_path FROM pages_fts JOIN pages ON pages.rel_path=pages_fts.rel_path "
        "WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts) LIMIT 3",
        ("memory",),
    ).fetchall()
    conn.close()
    assert rows, "fixture invalid: MATCH probe must still succeed"

    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)
    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["fts_before"]["corrupt"] is True
    assert "fts_rebuilt" in result["repairs"]
    assert result["ok"] is True


def test_repair_flag_gates_fts_rebuild(tmp_path, monkeypatch):
    mod, st = make_store(tmp_path)
    st.close()
    corrupt_unrelated_page(tmp_path / "cortex/.plugin.db", "decisions/memory-source.md")
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=False)

    assert result["ok"] is False
    assert result["repairs"] == []
    assert result["fts_after"]["corrupt"] is True


def test_backup_precedes_the_mutating_store_open(tmp_path, monkeypatch):
    """CortexStore's constructor mutates, so the recovery point must precede it.

    The backup must still contain the CORRUPT index: that proves it was taken
    before any repair, including the constructor's own healing.
    """
    mod, st = make_store(tmp_path)
    st.close()
    db = tmp_path / "cortex/.plugin.db"
    corrupt_unrelated_page(db, "decisions/memory-source.md")
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    backups = [b for b in result["backups"] if "bak-doctor-preopen-" in b]
    assert backups, "a pre-open backup is mandatory"
    backup_conn = sqlite3.connect(backups[0])
    with pytest.raises(sqlite3.DatabaseError):
        backup_conn.execute("INSERT INTO pages_fts(pages_fts, rank) VALUES('integrity-check', 1)")
    backup_conn.close()


def test_check_only_run_also_takes_a_backup(tmp_path, monkeypatch):
    """Even --repair=False opens the store, which mutates. Protect it anyway."""
    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=False)

    assert any("bak-doctor-preopen-" in b for b in result["backups"])


def test_backup_is_a_consistent_sqlite_snapshot(tmp_path, monkeypatch):
    """A raw file copy can be torn; the backup must be a readable database."""
    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    backup = Path(result["backups"][0])
    conn = sqlite3.connect(backup)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT count(*) FROM pages").fetchone()[0] == 2
    conn.close()


# ------------------------------------------------------------ data-loss gate


def test_missing_markdown_tree_aborts_before_opening_the_store(tmp_path, monkeypatch):
    """The confirmed data-loss path: a bad --store path wipes a good index.

    CortexStore's constructor DELETEs rows for files it cannot see, so a wrong
    path or unmounted volume silently empties the index -- on check-only runs
    too. The doctor must refuse to open the store at all.
    """
    mod, st = make_store(tmp_path, pages=6)
    st.close()
    db = tmp_path / "cortex/.plugin.db"
    before = sqlite3.connect(db).execute("SELECT count(*) FROM pages").fetchone()[0]
    assert before == 6

    for md in (tmp_path / "cortex").rglob("*.md"):
        md.unlink()

    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)
    with pytest.raises(mod.SetupError) as excinfo:
        mod.run(tmp_path / "cortex", tmp_path, "memory", repair=False)

    assert "corpus sanity check failed" in str(excinfo.value)
    after = sqlite3.connect(db).execute("SELECT count(*) FROM pages").fetchone()[0]
    assert after == before, "index must survive an aborted run"


def test_setup_failure_exits_2_not_1(tmp_path, monkeypatch, capsys):
    """Cron must distinguish 'bad invocation' from 'data is broken'."""
    mod = load_module()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)
    monkeypatch.setattr(
        sys_argv_holder := mod.sys, "argv",
        ["nightly_doctor.py", "--store", str(tmp_path / "nope"), "--profile-home", str(tmp_path)],
    )
    assert sys_argv_holder is mod.sys

    code = mod.main()

    assert code == mod.EXIT_SETUP == 2
    assert "setup_error" in capsys.readouterr().out


def test_normal_page_deletion_is_not_blocked(tmp_path, monkeypatch):
    """The sanity gate must not fire on ordinary curation deleting a few pages."""
    mod, st = make_store(tmp_path, pages=10)
    st.close()
    (tmp_path / "cortex/topics/memory-extra-0.md").unlink()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["ok"] is True
    assert result["embeddings_after"]["pages"] == 9


# --------------------------------------------------------------- embeddings


def test_empty_store_is_healthy_not_alarming(tmp_path, monkeypatch):
    """A new agent with no pages yet must not alarm every single night.

    Found in fleet rollout: a profile whose Cortex store was empty exited 1
    forever. Coverage asks whether the pages that EXIST are embedded.
    """
    mod = load_module()
    (tmp_path / "cortex").mkdir()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["embeddings_after"]["pages"] == 0
    assert result["embeddings_after"]["ok"] is True
    assert result["retrieval"]["ok"] is True
    assert result["ok"] is True
    assert result["repairs"] == []


def test_missing_embeddings_are_backfilled(tmp_path, monkeypatch):
    mod, st = make_store(tmp_path)
    st._conn.execute("DELETE FROM page_embeddings")
    st._conn.commit()
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["ok"] is True
    assert result["embeddings_before"]["embedded"] == 0
    assert result["embeddings_after"]["embedded"] == 2
    assert any(item.startswith("embeddings_backfilled:") for item in result["repairs"])


def test_embeddings_from_a_foreign_model_are_not_reported_healthy(tmp_path, monkeypatch):
    """Full count with vectors from an old model is a silent quality failure."""
    mod, st = make_store(tmp_path)
    st._conn.execute("UPDATE page_embeddings SET model='stale-model-v0'")
    st._conn.commit()
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=False)

    assert result["embeddings_before"]["embedded"] == 2
    assert result["embeddings_before"]["ok"] is False
    assert result["embeddings_before"]["foreign_model_rows"] == ["stale-model-v0"]
    assert result["ok"] is False


def test_unhealthy_endpoint_fails_closed_when_embeddings_missing(tmp_path, monkeypatch):
    mod, st = make_store(tmp_path)
    st._conn.execute("DELETE FROM page_embeddings")
    st._conn.commit()
    st.close()

    class DownEmbedder(FakeEmbedder):
        def health(self):
            return False

    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", DownEmbedder)
    # embed_url must be set so _semantic_configured returns True; otherwise a
    # store with no embeddings and no configured endpoint is legitimately lexical-only.
    result = mod.run(
        tmp_path / "cortex", tmp_path, "memory", repair=True,
        plugin_config={"embed_url": "http://embedder.test:1234/v1"},
    )

    assert result["ok"] is False
    assert result["embedding_endpoint_healthy"] is False
    assert not any(item.startswith("embeddings_backfilled:") for item in result["repairs"])


def test_semantic_config_gate_mirrors_provider(monkeypatch):
    mod = load_module()
    monkeypatch.setenv("CORTEX_EMBED_URL", "http://embed.test/v1")
    assert mod._semantic_configured({}) is True
    assert mod._semantic_configured({"semantic": "false"}) is False
    assert mod._semantic_configured({"semantic": "true"}) is True


# ---------------------------------------------------------------- retrieval


def test_post_repair_integrity_is_what_gates_success(tmp_path, monkeypatch):
    """Exit 0 must mean 'healthy NOW', proven after repairs, not before them.

    Checking integrity only up front would let a rebuild that damaged the file
    still report success.
    """
    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    calls = {"n": 0}

    class FlakyIntegrityConn:
        """Wraps the real connection; reports damage only on the POST-repair check."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "integrity_check" in sql:
                calls["n"] += 1
                if calls["n"] >= 2:
                    class Fake:
                        def fetchone(self_inner):
                            return ("row 42 missing from index pages_fts",)
                    return Fake()
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    real_get_conn = mod.CortexStore._get_conn
    monkeypatch.setattr(
        mod.CortexStore, "_get_conn",
        lambda self: FlakyIntegrityConn(real_get_conn(self)),
    )
    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert calls["n"] >= 2, "integrity must be re-checked AFTER repairs"
    assert result["sqlite_integrity_after"] != "ok"
    assert result["ok"] is False


def test_lexical_only_retrieval_is_not_reported_healthy(tmp_path, monkeypatch):
    """Silent degradation to lexical search is the failure this job exists to catch."""
    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)
    real_search = mod.CortexRetriever.search

    def lexical_only(self, query, **kwargs):
        rows = real_search(self, query, **kwargs)
        for row in rows:
            row["source"] = "fts"
        return rows

    monkeypatch.setattr(mod.CortexRetriever, "search", lexical_only)
    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["retrieval"]["count"] > 0
    assert result["retrieval"]["ok"] is False
    assert result["ok"] is False


# ------------------------------------------------------------ housekeeping


def test_old_backups_are_pruned(tmp_path, monkeypatch):
    """Nightly repairs must not fill the disk with database copies."""
    import os
    import time

    mod, st = make_store(tmp_path)
    st.close()
    stale = tmp_path / "cortex/.plugin.db.bak-doctor-preopen-20200101T000000"
    stale.write_bytes(b"old")
    old = time.time() - 40 * 86400
    os.utime(stale, (old, old))
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True, keep_backup_days=14)

    assert stale.name in result["pruned_backups"]
    assert not stale.exists()
    assert Path(result["backups"][0]).exists(), "today's backup must be kept"


def test_corpus_gate_ignores_files_the_indexer_skips(tmp_path, monkeypatch):
    """Decoy markdown in excluded dirs must not satisfy the data-loss gate.

    CortexStore prunes SKIP_DIRS (node_modules/, .git/, ...) when reindexing, so
    counting those files lets the gate pass while the constructor deletes every
    real page. Verified: this wiped a 6-page index before the fix.
    """
    mod, st = make_store(tmp_path, pages=6)
    st.close()
    store = tmp_path / "cortex"
    db = store / ".plugin.db"
    before = sqlite3.connect(db).execute("SELECT count(*) FROM pages").fetchone()[0]
    assert before == 6

    for md in store.rglob("*.md"):
        md.unlink()
    decoys = store / "node_modules"
    decoys.mkdir(parents=True)
    for i in range(20):
        (decoys / f"decoy{i}.md").write_text("# decoy\nmemory")
    # Filename-level exclusions count too: index.md, .bak, and dotfiles are
    # skipped by the indexer regardless of where they live.
    (store / "index.md").write_text("# skipped by the indexer")
    (store / "topics").mkdir(exist_ok=True)
    for i in range(10):
        (store / "topics" / f"note{i}.md.bak").write_text("# skipped")
        (store / "topics" / f".hidden{i}.md").write_text("# skipped")

    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)
    with pytest.raises(mod.SetupError):
        mod.run(store, tmp_path, "memory", repair=False)

    after = sqlite3.connect(db).execute("SELECT count(*) FROM pages").fetchone()[0]
    assert after == before, "index must survive: decoys are invisible to the indexer"


def test_store_and_db_path_come_from_config_yaml(tmp_path):
    """The doctor must inspect the SAME database the live provider uses.

    Reading only .env means probing a default path, so a profile with a custom
    store_path/db_path gets a freshly created database 'repaired' while the real
    one stays broken.
    """
    mod = load_module()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        "plugins:\n"
        "  cortex:\n"
        "    store_path: $HERMES_HOME/custom-kb\n"
        "    db_path: $HERMES_HOME/custom-kb/custom.db\n"
        "    embed_model: configured-model\n"
        "    embed_dim: 7\n"
    )

    cfg = mod.load_plugin_config(profile)
    store, db = mod.resolve_paths(profile, cfg, None)

    assert store == (profile / "custom-kb").resolve()
    assert db == (profile / "custom-kb/custom.db").resolve()
    assert cfg["embed_model"] == "configured-model"

    embedder = mod.build_embedder(cfg)
    assert embedder.model == "configured-model"
    assert embedder.dimensions == 7


def test_explicit_store_flag_overrides_config(tmp_path):
    """--store must override the database too, not just the markdown root.

    Pairing an overridden tree with the configured db_path would open the LIVE
    index against a different corpus, and the reindex would delete every page it
    could not find under the override.
    """
    mod = load_module()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        "plugins:\n"
        "  cortex:\n"
        "    store_path: $HERMES_HOME/from-config\n"
        "    db_path: $HERMES_HOME/from-config/live.db\n"
    )

    cfg = mod.load_plugin_config(profile)
    store, db = mod.resolve_paths(profile, cfg, str(tmp_path / "explicit"))

    assert store == (tmp_path / "explicit").resolve()
    assert db == (tmp_path / "explicit/.plugin.db").resolve()
    assert "from-config" not in str(db), "override must not reuse the live database"


def test_braced_hermes_home_is_expanded(tmp_path):
    """The provider expands ${HERMES_HOME} as well as $HERMES_HOME."""
    mod = load_module()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        "plugins:\n"
        "  cortex:\n"
        "    store_path: ${HERMES_HOME}/braced-kb\n"
        "    db_path: ${HERMES_HOME}/braced-kb/braced.db\n"
    )

    cfg = mod.load_plugin_config(profile)
    store, db = mod.resolve_paths(profile, cfg, None)

    assert store == (profile / "braced-kb").resolve()
    assert db == (profile / "braced-kb/braced.db").resolve()
    assert "$" not in str(store) and "$" not in str(db)


def test_embedding_dimension_drift_is_reported_and_forces_recompute(tmp_path, monkeypatch):
    """An endpoint can change vector width without changing its model name.

    Model-only checking leaves rows that match on name but are unusable at query
    time, so coverage looks full while semantic retrieval finds nothing.
    """
    mod, st = make_store(tmp_path)
    st._conn.execute("UPDATE page_embeddings SET dimensions=99")
    st._conn.commit()
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    checked = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=False)
    assert checked["embeddings_before"]["embedded"] == 2
    assert checked["embeddings_before"]["dimension_drift"], "width drift must be detected"
    assert checked["embeddings_before"]["ok"] is False
    assert checked["ok"] is False

    repaired = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)
    assert any(r.startswith("embeddings_backfilled:") for r in repaired["repairs"])
    assert repaired["embeddings_after"]["dimension_drift"] == []
    assert repaired["ok"] is True


def test_dotenv_handles_export_and_quotes(tmp_path, monkeypatch):
    mod = load_module()
    env = tmp_path / ".env"
    env.write_text('export CORTEX_TEST_A="value-a"\nCORTEX_TEST_B=\'value-b\'\n# comment\n')
    for key in ("CORTEX_TEST_A", "CORTEX_TEST_B"):
        monkeypatch.delenv(key, raising=False)

    mod.load_dotenv(env)

    import os
    assert os.environ["CORTEX_TEST_A"] == "value-a"
    assert os.environ["CORTEX_TEST_B"] == "value-b"


def test_non_markdown_payload_is_never_read_or_indexed(tmp_path, monkeypatch):
    mod, st = make_store(tmp_path)
    binary = tmp_path / "cortex/assets/payload.bin"
    binary.parent.mkdir()
    binary.write_bytes(b"\x00\xffnot text")
    before = binary.read_bytes()
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    result = mod.run(tmp_path / "cortex", tmp_path, "memory", repair=True)

    assert result["ok"] is True
    assert binary.read_bytes() == before
    conn = sqlite3.connect(tmp_path / "cortex/.plugin.db")
    assert conn.execute("SELECT count(*) FROM pages WHERE rel_path LIKE '%payload.bin%'").fetchone()[0] == 0
    conn.close()


# ------------------------------------------------- lexical-only deployments


def test_lexical_only_deployment_is_healthy(tmp_path, monkeypatch):
    """A store with no embed_url must not alarm the nightly job every single night.

    Before this fix: embedded == 0 with pages > 0 (no endpoint configured) caused
    the health gate to fail, and FTS-only retrieval was rejected as degradation.
    """
    mod = load_module()

    class NoEmbedder(FakeEmbedder):
        def health(self):
            return False

    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", NoEmbedder)
    store = tmp_path / "cortex"
    store.mkdir()
    (store / "topics").mkdir()
    (store / "topics" / "memory-ops.md").write_text("# Memory operations\nThis is about memory.")

    # plugin_config has no embed_url — pure lexical-only deployment.
    result = mod.run(store, tmp_path, "memory", repair=False, plugin_config={})

    assert result["ok"] is True, "lexical-only store must not alarm every night"
    assert result["embedding_endpoint_healthy"] is False
    assert result["repairs"] == []


# ----------------------------------------------- fts syntax error isolation


def test_fts_syntax_error_is_not_reported_as_corruption():
    """An OperationalError from a bad --query must not mark the FTS index as corrupt.

    The old code wrapped both the integrity-check and the MATCH probe in the
    same try-except, so FTS5 syntax errors (bad --query) set corrupt=True and
    triggered a rebuild on a perfectly healthy index.
    """
    mod = load_module()
    integrity_checked: list[bool] = []

    class FakeConn:
        def execute(self, sql, params=()):
            if "integrity-check" in sql:
                integrity_checked.append(True)
                return self
            if "MATCH" in sql:
                raise sqlite3.OperationalError("fts5: syntax error near 'AND'")
            return self

        def fetchall(self):
            return []

    result = mod.fts_probe(FakeConn(), "AND OR")

    assert result["corrupt"] is False, "FTS syntax error must not be flagged as corruption"
    assert result["ok"] is False
    assert integrity_checked, "integrity-check must run before the MATCH probe"
    assert "query syntax error" in (result["error"] or "")


# ----------------------------------------- backup pruning: custom db dir


def test_backup_pruning_covers_custom_db_directory(tmp_path, monkeypatch):
    """Backups written beside a custom db_path outside store_path must be pruned.

    prune_backups previously only globbed store_path, so backups beside a
    custom db_path accumulated forever when the database lived elsewhere.
    """
    import os
    import time

    mod, st = make_store(tmp_path)
    st.close()
    monkeypatch.setattr(mod, "OpenAIEmbeddingClient", FakeEmbedder)

    custom_db_dir = tmp_path / "dbs"
    custom_db_dir.mkdir()
    stale = custom_db_dir / ".plugin.db.bak-doctor-preopen-20200101T000000"
    stale.write_bytes(b"old backup in custom db dir")
    old_ts = time.time() - 40 * 86400
    os.utime(stale, (old_ts, old_ts))

    result = mod.run(
        tmp_path / "cortex",
        tmp_path,
        "memory",
        repair=False,
        keep_backup_days=14,
        db_path=custom_db_dir / "custom.db",
    )

    assert stale.name in result["pruned_backups"], "stale backup in custom db dir must be pruned"
    assert not stale.exists()
