"""Claude native paste envelopes shared by polling and completion hooks."""

from __future__ import annotations

import re


_OPEN_PASTE = re.compile(r'<pasted_content id="([^"<>\s]+)">')
_PASTE_TAG = re.compile(r'</?pasted_content\b', re.IGNORECASE)


def unwrap_claude_pasted_prompt(text: str) -> str:
    """Unwrap one complete native envelope, preserving all other input.

    This only normalizes transport syntax; callers must still require their
    request anchor at the start and establish top-level/current-turn ownership.
    IDs are opaque and case-sensitive. Do not recursively unwrap or accept
    sibling envelopes, which could turn quoted requests into active requests.
    """
    text = str(text or "")
    stripped = text.strip()
    opening = _OPEN_PASTE.match(stripped)
    if opening is None:
        return text
    closing = f'</pasted_content id="{opening.group(1)}">'
    if not stripped.endswith(closing):
        return text
    body = stripped[opening.end():-len(closing)]
    if _PASTE_TAG.search(body):
        return text
    return body
