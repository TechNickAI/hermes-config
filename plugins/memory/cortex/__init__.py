"""cortex — Hermes MemoryProvider plugin backed by a hand-curated markdown KB.

Cortex is a personal knowledge compiler: people, ventures, topics, decisions,
synthesis, learning, research — each a folder of markdown pages with YAML
frontmatter. This plugin wires it into the Hermes agent loop:

  • prefetch — search the KB before each turn and inject relevant pages
  • sync_turn — append meaningful turns to today's daily journal
  • on_session_end — summarize the session into synthesis/ if non-trivial
  • on_pre_compress — preserve durable facts before the compressor drops them
  • tools — `cortex` (search/read/write/list/daily) for explicit recall + capture

Config in $HERMES_HOME/config.yaml:

    plugins:
      cortex:
        store_path: $HERMES_HOME/cortex      # default: $HERMES_HOME/cortex
        db_path: ""                          # default: <store>/.plugin.db
        prefetch_limit: 5                    # results injected per turn
        auto_journal: false                  # sync_turn writes daily entries
        auto_synthesize: false               # on_session_end writes synthesis
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from hermes_cli.config import cfg_get

from .store import CortexStore, KNOWLEDGE_CATEGORIES, DAILY_DIR
from .retrieval import CortexRetriever
from .embeddings import OpenAIEmbeddingClient
from .handoff import build_handoff, handoff_slug

# Category (subdirectory) the pre-compress handoff digests are written under.
HANDOFF_CATEGORY = "handoff"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

CORTEX_TOOL_SCHEMA = {
    "name": "cortex",
    "description": (
        "Hand-curated personal knowledge base — people, ventures, topics, "
        "decisions, synthesis, learning, research. Pages are markdown with "
        "structured frontmatter. Search returns relevant pages with snippets; "
        "read returns the full page; write creates or updates a page.\n\n"
        "ACTIONS:\n"
        "  • search — full-text search (returns snippets + scores)\n"
        "  • read   — full body of a page by rel_path (e.g. 'people/nick.md')\n"
        "  • write  — create or overwrite a page in a category\n"
        "  • list   — list pages in a category (or all) by recency\n"
        "  • daily  — append a timestamped entry to today's journal\n\n"
        "Use this BEFORE asking the user about anything they might have told you "
        "before. Use 'write' to capture durable facts a future session should know."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "read", "write", "list", "daily"]},
            "query": {"type": "string", "description": "Search query (for 'search')."},
            "rel_path": {"type": "string", "description": "Page path like 'topics/foo.md' (for 'read')."},
            "category": {
                "type": "string",
                "description": (
                    "Category subdir (for 'write'/'list'). Any directory name works — "
                    "create new categories freely. Suggested starters: people, ventures, "
                    "projects, topics, synthesis, decisions, learning, research, daily."
                ),
            },
            "title": {"type": "string", "description": "Page title (for 'write')."},
            "slug": {"type": "string", "description": "Optional filename slug; defaults to title-derived."},
            "body": {"type": "string", "description": "Page body markdown (for 'write'/'daily')."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (for 'write')."},
            "limit": {"type": "integer", "description": "Result limit (default 5 for search, 20 for list)."},
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _config_bool(config: dict, key: str, default: bool = False) -> bool:
    """Return bool from config, handling string 'true'/'false' from setup prompts."""
    v = config.get(key, default)
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "yes", "1")


def _load_plugin_config() -> dict:
    """Load plugin config from $HERMES_HOME/config.yaml under plugins.cortex."""
    try:
        from hermes_constants import get_hermes_home
        config_path = get_hermes_home() / "config.yaml"
        if not config_path.exists():
            return {}
        import yaml
        with open(config_path, encoding="utf-8-sig") as f:
            all_config = yaml.safe_load(f) or {}
        return cfg_get(all_config, "plugins", "cortex", default={}) or {}
    except Exception as e:
        logger.debug("CortexProvider: config load failed: %s", e)
        return {}


def _resolve_path(value: str, hermes_home: str) -> str:
    """Expand $HERMES_HOME / ${HERMES_HOME} / ~ in user-provided paths."""
    if not value:
        return value
    out = value.replace("$HERMES_HOME", hermes_home).replace("${HERMES_HOME}", hermes_home)
    return str(Path(out).expanduser())


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class CortexMemoryProvider(MemoryProvider):
    """Markdown KB with FTS5 prefetch and lifecycle hooks."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store: Optional[CortexStore] = None
        self._retriever: Optional[CortexRetriever] = None
        self._session_id: str = ""
        # Topic identity for pre-compress handoff keying (captured at init).
        self._chat_id: str = ""
        self._thread_id: str = ""
        self._topic_label: str = ""

    @property
    def name(self) -> str:
        return "cortex"

    # -- Availability & init ----------------------------------------------

    def is_available(self) -> bool:
        return True  # SQLite always available; store dir is auto-created

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        hermes_home = kwargs.get("hermes_home") or str(get_hermes_home())
        default_store = str(Path(hermes_home) / "cortex")
        store_path = _resolve_path(self._config.get("store_path", default_store), hermes_home)
        db_path_raw = self._config.get("db_path") or ""
        db_path = _resolve_path(db_path_raw, hermes_home) if db_path_raw else None

        embedder = self._build_embedder()
        self._store = CortexStore(store_path=store_path, db_path=db_path, embedder=embedder)
        self._retriever = CortexRetriever(self._store)
        self._session_id = session_id
        # Capture topic identity for handoff keying. on_pre_compress() only
        # receives `messages`, so we must stash chat/thread context here.
        self._chat_id = str(kwargs.get("chat_id") or "")
        self._thread_id = str(kwargs.get("thread_id") or "")
        chat_name = str(kwargs.get("chat_name") or "").strip()
        self._topic_label = chat_name or self._chat_id or self._session_id
        stats = self._store.embedding_stats() if embedder else {"embedded": 0}
        logger.info(
            "Cortex memory: %d pages indexed at %s (semantic: %s, embedded=%d)",
            self._store.count(), store_path,
            "on" if embedder else "off", stats.get("embedded", 0),
        )

    def _build_embedder(self):
        """Construct the embedding client when the semantic tier is enabled.

        Enabled by default but fails safe: if the configured endpoint is
        unreachable at init, we log and fall back to lexical-only FTS5 so a down
        embedding host never breaks Cortex recall.
        """
        if not _config_bool(self._config, "semantic", default=True):
            return None
        try:
            client = OpenAIEmbeddingClient(
                url=self._config.get("embed_url") or None,
                model=self._config.get("embed_model") or None,
                api_key=self._config.get("embed_key") or None,
                dimensions=int(self._config["embed_dim"]) if self._config.get("embed_dim") else None,
            )
            if not client.url:
                logger.info("Cortex: no embed_url configured — semantic tier off, lexical-only")
                return None
            if not client.health():
                logger.warning(
                    "Cortex: embedding endpoint %s unreachable — semantic tier disabled this session",
                    client.url,
                )
                return None
            return client
        except Exception as e:
            logger.warning("Cortex: failed to init embedder (%s) — lexical-only", e)
            return None

    def shutdown(self) -> None:
        if self._store:
            self._store.close()
        self._store = None
        self._retriever = None

    # -- Config schema for `hermes memory setup` --------------------------

    def get_config_schema(self) -> list[dict]:
        try:
            from hermes_constants import display_hermes_home
            default_store = f"{display_hermes_home()}/cortex"
        except Exception:
            default_store = "$HERMES_HOME/cortex"
        return [
            {"key": "store_path", "description": "Filesystem path to the Cortex KB", "default": default_store},
            {"key": "prefetch_limit", "description": "Pages injected before each turn", "default": "5"},
            {"key": "auto_journal", "description": "Append meaningful turns to daily/", "default": "false", "choices": ["true", "false"]},
            {"key": "auto_synthesize", "description": "Write session summaries to synthesis/", "default": "false", "choices": ["true", "false"]},
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        """Write non-secret config to config.yaml under plugins.cortex."""
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing: dict = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["cortex"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.warning("Cortex: failed to save config: %s", e)

    # -- System prompt block ----------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store.count()
            cats = self._store.category_counts()
        except Exception:
            total, cats = 0, {}
        if total == 0:
            return (
                "# Cortex Memory\n"
                "Active. Empty KB. Use `cortex(action='write', category=..., title=..., body=...)` "
                "to capture durable knowledge the agent should remember across sessions."
            )
        breakdown = " · ".join(f"{c}={n}" for c, n in sorted(cats.items()))
        return (
            f"# Cortex Memory\n"
            f"Active. {total} pages indexed ({breakdown}). Relevant pages are prefetched "
            f"automatically each turn. Use the `cortex` tool to search/read/write explicitly."
        )

    # -- Prefetch ----------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not query:
            return ""
        limit = int(self._config.get("prefetch_limit", 5))
        try:
            results = self._retriever.search(query, limit=limit)
        except Exception as e:
            logger.debug("Cortex prefetch failed: %s", e)
            return ""
        if not results:
            return ""
        lines = ["## Cortex (relevant pages)"]
        for r in results:
            snip = (r.get("snippet") or "").replace("\n", " ").strip()
            lines.append(f"- **{r['title']}** (`{r['rel_path']}`) — {snip}")
        return "\n".join(lines)

    # -- Sync turn ---------------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._store:
            return
        if not _config_bool(self._config, "auto_journal"):
            return
        # Heuristic: skip trivial turns
        if len(user_content) < 40 and len(assistant_content) < 80:
            return
        try:
            entry = f"**User:** {user_content.strip()[:500]}\n\n**Me:** {assistant_content.strip()[:800]}"
            self._store.append_daily(entry)
        except Exception as e:
            logger.debug("Cortex sync_turn failed: %s", e)

    # -- End of session ----------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._store:
            return
        if not _config_bool(self._config, "auto_synthesize"):
            return
        if not messages or len(messages) < 4:
            return
        # Build a lightweight session summary; LLM-driven extraction is the
        # cortex CLI's job (nightly cron) — here we just deposit a raw trail.
        try:
            now = datetime.now()
            chunks: list[str] = []
            for m in messages[-20:]:
                role = m.get("role", "?")
                content = m.get("content", "")
                if not isinstance(content, str):
                    continue
                if role not in ("user", "assistant"):
                    continue
                snippet = content.strip()[:400]
                if snippet:
                    chunks.append(f"**{role}:** {snippet}")
            if not chunks:
                return
            body = "\n\n".join(chunks)
            title = f"Session {now.strftime('%Y-%m-%d %H:%M')}"
            self._store.write_page(
                category="synthesis",
                slug_or_title=f"session-{now.strftime('%Y%m%d-%H%M')}",
                body=body,
                tags=["session", "auto"],
                title=title,
            )
        except Exception as e:
            logger.debug("Cortex on_session_end failed: %s", e)

    # -- Pre-compress ------------------------------------------------------

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Capture a handoff digest before compression drops old messages.

        Two jobs, both fail-safe (any error degrades to the lightweight KB
        reminder, never blocks compaction):

          1. Build a deterministic, no-LLM handoff digest (GOAL / STATE /
             DECISIONS / OPEN LOOPS / ARTIFACTS) from the live messages and
             write it to a topic-keyed page under `handoff/`. Because the store
             indexes it into FTS5, the normal prefetch hook re-pulls it on the
             next turn — so the digest both survives compaction on disk *and*
             gets re-injected into context automatically.
          2. Return a short markdown block (the digest summary + a recall
             pointer) to fold into the compression window immediately.
        """
        if not self._store:
            return ""
        try:
            count = self._store.count()
            cats = self._store.category_counts()
        except Exception:
            return ""
        if count == 0 and not messages:
            return ""

        breakdown = ", ".join(f"{c}={n}" for c, n in sorted(cats.items()))
        reminder = (
            f"\n[Cortex KB still available: {count} pages ({breakdown}). "
            f"Use `cortex(action='search', query=...)` to recall.]\n"
        )

        # Write a handoff for any session with enough substance. build_handoff()
        # returns "" for trivial threads, so it — not a gateway/CLI distinction —
        # decides whether a digest is worth persisting. Keying prefers the
        # durable topic (chat+thread) and falls back to session_id, so CLI
        # sessions are covered too.
        if not messages:
            return reminder

        try:
            now = datetime.now()
            body = build_handoff(
                messages,
                topic_label=self._topic_label,
                now_str=now.strftime("%Y-%m-%d %H:%M"),
            )
            if not body:
                return reminder
            slug = handoff_slug(
                chat_id=self._chat_id,
                thread_id=self._thread_id,
                session_id=self._session_id,
            )
            label = self._topic_label or slug
            rel = self._store.write_page(
                category=HANDOFF_CATEGORY,
                slug_or_title=slug,
                body=body,
                tags=["handoff", "auto", "pre-compress"],
                title=f"Handoff — {label}",
            )
            logger.info("Cortex handoff written: %s", rel)
            return (
                f"\n[Cortex handoff saved to `{rel}` before compaction — "
                f"the active task spine (GOAL/STATE/DECISIONS/NEXT MOVE) is "
                f"preserved and will be auto-recalled. "
                f"Use `cortex(action='read', rel_path='{rel}')` for the full "
                f"digest.]\n{reminder}"
            )
        except Exception as e:
            logger.debug("Cortex handoff write failed: %s", e)
            return reminder

    # -- Tool surface ------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [CORTEX_TOOL_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name != "cortex":
            return tool_error(f"Unknown tool: {tool_name}")
        if not self._store or not self._retriever:
            return tool_error("Cortex store not initialized")
        try:
            action = args["action"]
            if action == "search":
                q = args.get("query", "")
                if not q:
                    return tool_error("search requires 'query'")
                limit = int(args.get("limit", 5))
                results = self._retriever.search(q, limit=limit, category=args.get("category"))
                return json.dumps({"results": results, "count": len(results)})
            elif action == "read":
                rel = args.get("rel_path", "")
                if not rel:
                    return tool_error("read requires 'rel_path'")
                page = self._store.get_page(rel)
                if page is None:
                    return tool_error(f"Page not found: {rel}")
                return json.dumps(page)
            elif action == "write":
                category = args.get("category", "topics")
                title = args.get("title", "")
                slug = args.get("slug") or title
                body = args.get("body", "")
                tags = args.get("tags", []) or []
                if not slug:
                    return tool_error("write requires 'title' or 'slug'")
                rel = self._store.write_page(
                    category=category,
                    slug_or_title=slug,
                    body=body,
                    tags=tags,
                    title=title or None,
                )
                return json.dumps({"rel_path": rel, "status": "written"})
            elif action == "list":
                category = args.get("category")
                limit = int(args.get("limit", 20))
                pages = self._store.list_pages(category=category, limit=limit)
                return json.dumps({"pages": pages, "count": len(pages)})
            elif action == "daily":
                body = args.get("body", "")
                if not body:
                    return tool_error("daily requires 'body'")
                rel = self._store.append_daily(body)
                return json.dumps({"rel_path": rel, "status": "appended"})
            else:
                return tool_error(f"Unknown action: {action}")
        except KeyError as e:
            return tool_error(f"Missing required argument: {e}")
        except Exception as e:
            logger.exception("Cortex tool failed")
            return tool_error(str(e))


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the cortex memory provider with the plugin system."""
    config = _load_plugin_config()
    provider = CortexMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
