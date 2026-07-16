"""Apply saved pronunciation substitutions to text before it is sent to a
TTS engine. This never touches the essay text stored in the database or
shown on screen -- only the copy handed to the speech engine.

Because substitutions (and XML escaping for SSML) change character
positions, this module also produces offset maps so word-boundary events
reported by an engine can be translated back to positions in the original
displayed text for highlighting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_XML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
}


@dataclass
class Segment:
    """Maps [new_start, new_end) in the transformed text to
    [orig_start, orig_end) in the original text. `literal` means the
    segment is unchanged text (1:1 offset shift); otherwise any position
    inside it maps to the whole original span (a replaced region).
    """

    new_start: int
    new_end: int
    orig_start: int
    orig_end: int
    literal: bool


class OffsetMap:
    def __init__(self, segments: list[Segment]):
        self.segments = segments

    def to_original(self, new_start: int, new_end: int) -> tuple[int, int]:
        return (self._pos_to_original(new_start, False), self._pos_to_original(new_end, True))

    def _pos_to_original(self, pos: int, is_end: bool) -> int:
        for seg in self.segments:
            if seg.new_start <= pos <= seg.new_end:
                if pos == seg.new_end and not is_end:
                    continue  # prefer the following segment for a start position
                if seg.literal:
                    return seg.orig_start + (pos - seg.new_start)
                return seg.orig_end if is_end else seg.orig_start
        if self.segments:
            return self.segments[-1].orig_end
        return pos

    def compose(self, inner: "OffsetMap") -> "OffsetMap":
        """Return a map that first applies self's translation, then inner's.

        Used when text goes through two transforms (substitute, then
        escape): outer maps escaped->substituted, inner maps
        substituted->original.
        """
        outer = self

        class _Composed(OffsetMap):
            def __init__(self):
                pass

            def to_original(self, new_start: int, new_end: int) -> tuple[int, int]:
                mid = outer.to_original(new_start, new_end)
                return inner.to_original(mid[0], mid[1])

        return _Composed()


def apply_substitutions_with_map(text: str, rules: list[dict]) -> tuple[str, OffsetMap]:
    """Replace occurrences of each rule's find_text with its replace_text
    in a single pass, returning the new text plus an offset map back to the
    original. Longer find-strings win when matches overlap (so "Mr Darcy"
    beats "Darcy"). Matching is case-insensitive; whole_word rules only
    match on word boundaries.
    """
    matches: list[tuple[int, int, str]] = []  # (start, end, replacement)
    taken: list[tuple[int, int]] = []
    for rule in sorted(rules, key=lambda r: len(r.get("find_text", "")), reverse=True):
        find_text = (rule.get("find_text") or "").strip()
        if not find_text:
            continue
        replace_text = rule.get("replace_text") or ""
        pattern = re.escape(find_text)
        if rule.get("whole_word", True):
            pattern = r"\b" + pattern + r"\b"
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            matches.append((m.start(), m.end(), replace_text))

    matches.sort(key=lambda t: t[0])

    out_parts: list[str] = []
    segments: list[Segment] = []
    cursor = 0
    new_pos = 0
    for start, end, replacement in matches:
        if start > cursor:
            literal = text[cursor:start]
            out_parts.append(literal)
            segments.append(Segment(new_pos, new_pos + len(literal), cursor, start, True))
            new_pos += len(literal)
        out_parts.append(replacement)
        segments.append(Segment(new_pos, new_pos + len(replacement), start, end, False))
        new_pos += len(replacement)
        cursor = end
    if cursor < len(text):
        literal = text[cursor:]
        out_parts.append(literal)
        segments.append(Segment(new_pos, new_pos + len(literal), cursor, len(text), True))

    return "".join(out_parts), OffsetMap(segments)


def apply_substitutions(text: str, rules: list[dict]) -> str:
    new_text, _ = apply_substitutions_with_map(text, rules)
    return new_text


def escape_xml_with_map(text: str) -> tuple[str, OffsetMap]:
    """XML-escape text, returning an offset map from escaped positions back
    to unescaped positions (needed because engines report word boundaries
    against the markup string they were given).
    """
    out_parts: list[str] = []
    segments: list[Segment] = []
    new_pos = 0
    run_start = 0
    for i, ch in enumerate(text):
        esc = _XML_ESCAPES.get(ch)
        if esc is None:
            continue
        if i > run_start:
            run = text[run_start:i]
            segments.append(Segment(new_pos, new_pos + len(run), run_start, i, True))
            out_parts.append(run)
            new_pos += len(run)
        segments.append(Segment(new_pos, new_pos + len(esc), i, i + 1, False))
        out_parts.append(esc)
        new_pos += len(esc)
        run_start = i + 1
    if run_start < len(text):
        run = text[run_start:]
        segments.append(Segment(new_pos, new_pos + len(run), run_start, len(text), True))
        out_parts.append(run)
    return "".join(out_parts), OffsetMap(segments)


def escape_ssml(text: str) -> str:
    escaped, _ = escape_xml_with_map(text)
    return escaped
