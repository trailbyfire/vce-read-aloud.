"""Unit tests for pronunciation substitution + offset mapping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pronunciation import (  # noqa: E402
    apply_substitutions,
    apply_substitutions_with_map,
    escape_xml_with_map,
)


def test_basic_substitution():
    rules = [{"find_text": "Wemmick", "replace_text": "WEM-ick", "whole_word": 1}]
    out = apply_substitutions("Dickens uses Wemmick well.", rules)
    assert out == "Dickens uses WEM-ick well."


def test_case_insensitive_and_whole_word():
    rules = [{"find_text": "cat", "replace_text": "katt", "whole_word": 1}]
    assert apply_substitutions("Cat catalogue cat.", rules) == "katt catalogue katt."


def test_longer_rule_wins():
    rules = [
        {"find_text": "Darcy", "replace_text": "DAR-see", "whole_word": 1},
        {"find_text": "Mr Darcy", "replace_text": "Mister DAR-see", "whole_word": 1},
    ]
    out = apply_substitutions("Mr Darcy and Darcy.", rules)
    assert out == "Mister DAR-see and DAR-see."


def test_offset_map_roundtrip():
    text = "Dickens uses Wemmick to contrast selves."
    rules = [{"find_text": "Wemmick", "replace_text": "WEM-ick-ee", "whole_word": 1}]
    new_text, mapping = apply_substitutions_with_map(text, rules)
    assert new_text == "Dickens uses WEM-ick-ee to contrast selves."

    # A word before the substitution maps 1:1.
    start = new_text.index("uses")
    orig = mapping.to_original(start, start + 4)
    assert text[orig[0]:orig[1]] == "uses"

    # The substituted word maps back to the whole original word.
    start = new_text.index("WEM-ick-ee")
    orig = mapping.to_original(start, start + len("WEM-ick-ee"))
    assert text[orig[0]:orig[1]] == "Wemmick"

    # A word after the substitution (offsets shifted) still maps correctly.
    start = new_text.index("contrast")
    orig = mapping.to_original(start, start + len("contrast"))
    assert text[orig[0]:orig[1]] == "contrast"


def test_escape_map():
    text = 'Tom & Jerry say "hi" <now>.'
    escaped, mapping = escape_xml_with_map(text)
    assert "&amp;" in escaped and "&lt;now&gt;" in escaped

    start = escaped.index("Jerry")
    orig = mapping.to_original(start, start + 5)
    assert text[orig[0]:orig[1]] == "Jerry"

    start = escaped.index("&lt;now&gt;")
    orig = mapping.to_original(start, start + len("&lt;now&gt;"))
    assert text[orig[0]:orig[1]] == "<now>"


def test_multiword_replacement_composed_with_escape():
    # Simulates the full pipeline an engine sees: substitute, then escape.
    text = "Keneally & Grenville."
    rules = [{"find_text": "Keneally", "replace_text": "Kuh-nee-lee", "whole_word": 1}]
    substituted, sub_map = apply_substitutions_with_map(text, rules)
    escaped, esc_map = escape_xml_with_map(substituted)
    full_map = esc_map.compose(sub_map)

    start = escaped.index("Grenville")
    orig = full_map.to_original(start, start + len("Grenville"))
    assert text[orig[0]:orig[1]] == "Grenville"

    start = escaped.index("Kuh-nee-lee")
    orig = full_map.to_original(start, start + len("Kuh-nee-lee"))
    assert text[orig[0]:orig[1]] == "Keneally"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("ALL PRONUNCIATION TESTS PASSED")
