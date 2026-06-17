"""Centralized JSON-safe serialization for cortex tool responses.

Cortex pages carry YAML frontmatter parsed by ``yaml.safe_load``, which resolves
bare ``YYYY-MM-DD`` scalars to ``datetime.date`` and ISO timestamps to
``datetime.datetime``. The standard library JSON encoder cannot serialize those
types, so any tool response that echoes raw frontmatter (notably ``read``) raised
``TypeError: Object of type date is not JSON serializable``.

All cortex tool responses funnel their payload through :func:`dumps_response`,
which uses a date-aware encoder to convert ``date``/``datetime``/``time`` values
to stable ISO-8601 strings at the response boundary. This keeps the response
shape unchanged (the dict structure is identical) while guaranteeing the result
is JSON serializable.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any


class _DateAwareEncoder(json.JSONEncoder):
    """JSON encoder that renders date/datetime/time values as ISO strings."""

    def default(self, o: Any) -> Any:
        # datetime is a subclass of date, so order does not matter here:
        # isoformat() yields the richest stable representation for each type.
        if isinstance(o, (date, datetime, time)):
            return o.isoformat()
        # A YAML !!set tag in frontmatter resolves to a Python set, which the JSON
        # encoder cannot serialize. Emit a deterministic sorted list (keyed by str so
        # mixed-type sets never raise) so the read path can't crash on exotic frontmatter.
        if isinstance(o, (set, frozenset)):
            return sorted(o, key=str)
        return super().default(o)


def dumps_response(payload: Any) -> str:
    """Serialize a cortex tool response payload to a JSON string, safely.

    Converts ``datetime.date`` / ``datetime.datetime`` / ``datetime.time``
    values (e.g. parsed frontmatter fields) to ISO-8601 strings, and ``set`` /
    ``frozenset`` values (e.g. a YAML ``!!set`` tag) to deterministic sorted
    lists, rather than raising ``TypeError``. The payload's structure is
    otherwise preserved.
    """

    return json.dumps(payload, cls=_DateAwareEncoder)
