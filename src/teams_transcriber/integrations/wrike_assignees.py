"""Fuzzy + LLM assignee resolver for action_items_others.

Two-pass:
1. Token-sort-ratio against every contact's full name. If best score >= 0.85
   and beats the runner-up by >= 0.10, we take it.
2. (Optional) one batched Claude tool-use call resolves the remaining items
   using meeting summary + the action-item text as context.

The LLM pass is gated on `llm_fallback`. Network/API errors in the LLM call
log a WARNING and treat all unresolved items as None — the planner UI still
opens; the user can pick assignees manually.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Tuning knobs. Conservative thresholds; we'd rather punt to "Unassigned"
# than silently pick the wrong person.
_FUZZY_MIN_SCORE = 0.85
_FUZZY_MIN_MARGIN = 0.10

ItemKey = str  # opaque key the caller uses to identify each item
ClientFactory = Callable[[str], Any]


@dataclass(slots=True, frozen=True)
class Contact:
    id: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


# --- Token-sort-ratio (hand-rolled; rapidfuzz isn't in the dep tree) ---

# A distinct name token pair below this character-similarity is incidental
# letter overlap (e.g. "stone" vs "sarah"), not a real match — floor it to 0
# so disjoint full names don't accumulate a misleading partial score.
_TOKEN_NOISE_FLOOR = 0.8
# Minimum length for a prefix to count as a confident token hit ("jen" of
# "jennifer"). Shorter prefixes are too ambiguous.
_MIN_PREFIX_LEN = 3


def _tokens(s: str) -> list[str]:
    return [t for t in s.lower().split() if t]


def _token_best(query_tok: str, contact_tok: str) -> float:
    """Similarity of one query token to one contact token, in [0.0, 1.0].

    Order-independent by construction (called per-token). Exact match and
    meaningful prefixes (a first-name shorthand like "Jen" → "Jennifer") count
    as confident hits; otherwise fall back to character-sequence similarity with
    a noise floor so unrelated name tokens score 0."""
    from difflib import SequenceMatcher
    if query_tok == contact_tok:
        return 1.0
    if len(query_tok) >= _MIN_PREFIX_LEN and contact_tok.startswith(query_tok):
        return 1.0
    if len(contact_tok) >= _MIN_PREFIX_LEN and query_tok.startswith(contact_tok):
        return 1.0
    ratio = SequenceMatcher(None, query_tok, contact_tok).ratio()
    return ratio if ratio >= _TOKEN_NOISE_FLOOR else 0.0


def token_sort_ratio(a: str, b: str) -> float:
    """Score in [0.0, 1.0]. Token-aware fuzzy match between two names.

    Each token of `a` is matched against its best counterpart token in `b`
    (so word order does not matter — "Jennifer Smith" == "Smith Jennifer"), and
    the per-token scores are averaged. Exact tokens and meaningful prefixes
    ("Jen" → "Jennifer") score 1.0; near-typos score high; unrelated name tokens
    score 0. This is closer to rapidfuzz's `token_sort_ratio` semantics than a
    plain ``SequenceMatcher`` over the concatenated sorted strings, which cannot
    surface a first-name-only shorthand."""
    if not a or not b:
        return 0.0
    a_toks = _tokens(a)
    b_toks = _tokens(b)
    if not a_toks or not b_toks:
        return 0.0
    scores = [max(_token_best(qt, ct) for ct in b_toks) for qt in a_toks]
    return sum(scores) / len(scores)


def _fuzzy_resolve(name: str, contacts: Sequence[Contact]) -> str | None:
    """Best contact for `name`, or None if not confident enough."""
    if not name.strip() or not contacts:
        return None
    scored = sorted(
        ((token_sort_ratio(name, c.full_name), c) for c in contacts),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best_contact = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < _FUZZY_MIN_SCORE:
        return None
    if best_score - runner_up_score < _FUZZY_MIN_MARGIN:
        return None
    return best_contact.id


# --- LLM fallback ---

_TOOL_NAME = "resolve_assignees"
_TOOL = {
    "name": _TOOL_NAME,
    "description": (
        "For each unresolved action-item, choose the best-matching team-member "
        "id from the provided contacts, or null when no team member is a "
        "confident fit."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_index": {"type": "integer"},
                        "contact_id": {"type": ["string", "null"]},
                    },
                    "required": ["item_index", "contact_id"],
                },
            },
        },
        "required": ["matches"],
    },
}


def _default_client_factory(api_key: str) -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _llm_resolve(
    unresolved: list[tuple[int, str, str]],   # [(idx, who, task_text), ...]
    contacts: Sequence[Contact],
    *,
    meeting_summary: str,
    api_key: str,
    model: str,
    client_factory: ClientFactory,
) -> dict[int, str | None]:
    """Single batched Claude call. Returns {idx: contact_id|None}."""
    if not unresolved:
        return {}
    items_block = "\n".join(
        f"- index={i}  who={who!r}  task={task!r}"
        for i, who, task in unresolved
    )
    contacts_block = "\n".join(
        f"- {c.id}  {c.full_name}" for c in contacts
    )
    user_text = (
        "Meeting summary (for context):\n"
        f"{meeting_summary or '(none provided)'}\n\n"
        "Unresolved action items:\n"
        f"{items_block}\n\n"
        "Team members:\n"
        f"{contacts_block}\n\n"
        "For each unresolved item, call resolve_assignees with the matches."
    )
    try:
        client = client_factory(api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception:
        logger.warning("assignee LLM resolver failed; treating all unresolved as None",
                       exc_info=True)
        return {idx: None for idx, _, _ in unresolved}

    # Find the tool_use block.
    payload: dict[str, Any] | None = None
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == _TOOL_NAME:
            raw = getattr(block, "input", None)
            if isinstance(raw, dict):
                payload = raw
                break
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                    break
                except json.JSONDecodeError:
                    pass

    out: dict[int, str | None] = {idx: None for idx, _, _ in unresolved}
    if not payload:
        return out
    valid_ids = {c.id for c in contacts}
    for m in payload.get("matches", []) or []:
        try:
            idx = int(m["item_index"])
            cid_raw = m.get("contact_id")
        except (KeyError, TypeError, ValueError):
            continue
        if cid_raw is None:
            out[idx] = None
        elif isinstance(cid_raw, str) and cid_raw in valid_ids:
            out[idx] = cid_raw
        # else: ignore garbage ids (model hallucinated)
    return out


# --- Public entry point ---

def suggest_assignees(
    items: Sequence[tuple[ItemKey, str]],     # [(item_key, raw_who), ...]
    contacts: Sequence[Contact],
    *,
    meeting_summary: str | None,
    api_key: str | None,
    model: str,
    llm_fallback: bool,
    anthropic_client_factory: ClientFactory | None = None,
) -> dict[ItemKey, str | None]:
    """Return {item_key: contact_id or None} for each input item."""
    if not items:
        return {}
    factory = anthropic_client_factory or _default_client_factory

    keys: list[ItemKey] = [k for k, _ in items]

    resolved: dict[ItemKey, str | None] = {}
    unresolved_for_llm: list[tuple[int, str, str]] = []

    for i, (key, who) in enumerate(items):
        if not who or not who.strip():
            resolved[key] = None
            continue
        hit = _fuzzy_resolve(who, contacts)
        if hit is not None:
            resolved[key] = hit
        else:
            resolved[key] = None
            unresolved_for_llm.append((i, who, who))

    if llm_fallback and api_key and unresolved_for_llm:
        llm_out = _llm_resolve(
            unresolved_for_llm, contacts,
            meeting_summary=meeting_summary or "",
            api_key=api_key, model=model,
            client_factory=factory,
        )
        for i, hit in llm_out.items():
            if 0 <= i < len(keys):
                resolved[keys[i]] = hit

    return resolved
