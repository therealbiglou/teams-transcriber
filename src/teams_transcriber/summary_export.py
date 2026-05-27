"""Qt-free serialization of a Summary to markdown / plaintext / HTML.

Used by the Export action (md/txt/pdf) and the summary-pane Copy button, so the
output stays consistent and there is a single source of truth. `todo_states`
maps todo_index -> done (from TodoStateRepo); todos render with completion.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime

from teams_transcriber.storage.models import Recording, Summary

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    text = _TAG_RE.sub("", s)
    return _html.unescape(text)


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d, %Y, %I:%M %p")
    except ValueError:
        return iso


def _meta_line(summary: Summary, recording: Recording) -> str:
    minutes = (recording.duration_ms or 0) / 60000
    return f"{_fmt_time(recording.started_at)} · {minutes:.0f} min · {summary.model_used}"


def _title(summary: Summary, recording: Recording) -> str:
    return recording.display_title or summary.title or "Meeting"


def to_markdown(summary: Summary, recording: Recording, todo_states: dict[int, bool]) -> str:
    lines = [f"# {_title(summary, recording)}", "", f"_{_meta_line(summary, recording)}_", ""]
    if summary.summary:
        lines += [summary.summary, ""]
    if summary.my_todos:
        lines.append("## My todos")
        for i, t in enumerate(summary.my_todos):
            box = "x" if todo_states.get(i) else " "
            lines.append(f"- [{box}] {t.task}" + (f" (due {t.due})" if t.due else ""))
        lines.append("")
    if summary.action_items_others:
        lines.append("## Action items for others")
        for a in summary.action_items_others:
            lines.append(f"- {a.who}: {a.task}" + (f" (due {a.due})" if a.due else ""))
        lines.append("")
    if summary.key_decisions:
        lines.append("## Key decisions")
        lines += [f"- {d}" for d in summary.key_decisions]
        lines.append("")
    if summary.follow_ups:
        lines.append("## Follow-ups")
        lines += [f"- {f}" for f in summary.follow_ups]
        lines.append("")
    if summary.topics:
        lines.append("## Topics")
        lines.append(", ".join(summary.topics))
        lines.append("")
    notes = _strip_html(recording.manual_notes)
    if notes:
        lines += ["## My notes", notes, ""]
    return "\n".join(lines).rstrip() + "\n"


def to_plaintext(summary: Summary, recording: Recording, todo_states: dict[int, bool]) -> str:
    lines = [_title(summary, recording), _meta_line(summary, recording), ""]
    if summary.summary:
        lines += [summary.summary, ""]
    if summary.my_todos:
        lines.append("My todos")
        for i, t in enumerate(summary.my_todos):
            box = "[x]" if todo_states.get(i) else "[ ]"
            lines.append(f"  {box} {t.task}" + (f" (due {t.due})" if t.due else ""))
        lines.append("")
    if summary.action_items_others:
        lines.append("Action items for others")
        for a in summary.action_items_others:
            lines.append(f"  - {a.who}: {a.task}" + (f" (due {a.due})" if a.due else ""))
        lines.append("")
    if summary.key_decisions:
        lines.append("Key decisions")
        lines += [f"  - {d}" for d in summary.key_decisions]
        lines.append("")
    if summary.follow_ups:
        lines.append("Follow-ups")
        lines += [f"  - {f}" for f in summary.follow_ups]
        lines.append("")
    if summary.topics:
        lines += ["Topics", "  " + ", ".join(summary.topics), ""]
    notes = _strip_html(recording.manual_notes)
    if notes:
        lines += ["My notes", notes, ""]
    return "\n".join(lines).rstrip() + "\n"


def to_html(summary: Summary, recording: Recording, todo_states: dict[int, bool]) -> str:
    e = _html.escape
    parts = [
        "<html><head><meta charset='utf-8'></head>",
        "<body style=\"font-family: 'Segoe UI', sans-serif; color:#111827;\">",
        f"<h1 style='color:#065F46;'>{e(_title(summary, recording))}</h1>",
        f"<p style='color:#6B7280;font-size:12px;'>{e(_meta_line(summary, recording))}</p>",
    ]
    if summary.summary:
        parts.append(f"<p>{e(summary.summary)}</p>")
    if summary.my_todos:
        parts.append("<h2 style='color:#065F46;'>My todos</h2><ul>")
        for i, t in enumerate(summary.my_todos):
            mark = "☑" if todo_states.get(i) else "☐"
            due = f" (due {e(t.due)})" if t.due else ""
            parts.append(f"<li>{mark} {e(t.task)}{due}</li>")
        parts.append("</ul>")
    if summary.action_items_others:
        parts.append("<h2 style='color:#065F46;'>Action items for others</h2><ul>")
        for a in summary.action_items_others:
            due = f" (due {e(a.due)})" if a.due else ""
            parts.append(f"<li>{e(a.who)}: {e(a.task)}{due}</li>")
        parts.append("</ul>")
    if summary.key_decisions:
        parts.append("<h2 style='color:#065F46;'>Key decisions</h2><ul>")
        parts += [f"<li>{e(d)}</li>" for d in summary.key_decisions]
        parts.append("</ul>")
    if summary.follow_ups:
        parts.append("<h2 style='color:#065F46;'>Follow-ups</h2><ul>")
        parts += [f"<li>{e(f)}</li>" for f in summary.follow_ups]
        parts.append("</ul>")
    if summary.topics:
        parts.append("<h2 style='color:#065F46;'>Topics</h2>")
        parts.append(f"<p>{e(', '.join(summary.topics))}</p>")
    if recording.manual_notes:
        parts.append("<h2 style='color:#065F46;'>My notes</h2>")
        parts.append(recording.manual_notes)
    parts.append("</body></html>")
    return "".join(parts)
