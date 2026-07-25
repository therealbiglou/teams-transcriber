"""Pure Markdown builders for the Wrike project export (no client, no db)."""

from __future__ import annotations

from teams_transcriber.storage.models import Summary, TranscriptSegment


def build_description(summary: Summary) -> str:
    body = summary.summary or ""
    if summary.key_decisions:
        lines = "\n".join(f"- {d}" for d in summary.key_decisions)
        return f"{body}\n\n## Key decisions\n{lines}"
    return body


def _mmss(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_transcript_md(segments: list[TranscriptSegment]) -> str:
    if not segments:
        return "# Transcript\n\n_(no transcript)_"
    lines = [
        f"**{s.channel.value.upper()}** {_mmss(s.start_ms)} {s.text}"
        for s in segments
    ]
    return "# Transcript\n\n" + "\n".join(lines)
