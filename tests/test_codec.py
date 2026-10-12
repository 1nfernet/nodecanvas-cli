import random

from nodecanvas_cli import fsjson
from nodecanvas_cli.yamlscalar import BEST_WIDTH, encode, find_scalar


def roundtrip(value, indent=2, key="_serializedGraph"):
    lines = ["MonoBehaviour:"] + encode(key, indent, value) + ["  _objectReferences: []"]
    span = find_scalar(lines, key)
    assert span is not None and span.style in ("single", "double")
    return span.value, lines


def test_short_value_single_line():
    value, lines = roundtrip('{"a":1}')
    assert value == '{"a":1}'
    assert lines[1] == "  _serializedGraph: '{\"a\":1}'"


def test_quotes_are_doubled():
    value, lines = roundtrip("it's")
    assert value == "it's"
    assert "''" in lines[1]


def test_folds_only_on_single_spaces_past_width():
    text = " ".join(["word"] * 60)
    value, lines = roundtrip(text)
    assert value == text
    body = lines[1:-1]
    assert len(body) > 1
    assert all(line.startswith("    ") for line in body[1:])


def test_runs_of_spaces_and_newlines_survive():
    text = "a" * 70 + "  b    c" + " " * 9 + "d\r\n    e\n\nf" + " x" * 50
    value, _ = roundtrip(text)
    assert value == text


def test_json_like_strings_use_unity_folding():
    rnd = random.Random(99)
    for _ in range(300):
        text = "".join(rnd.choice("ab '\"{}:,\\") for _ in range(rnd.randint(1, 400))).strip(" ")
        lines = ["MonoBehaviour:"] + encode("_serializedGraph", 2, text) + ["  x: 1"]
        span = find_scalar(lines, "_serializedGraph")
        assert span.style == "single" and span.value == text, repr(text)


def test_random_strings_roundtrip():
    rnd = random.Random(1234)
    alphabet = "ab '\n\r\t\"\\{}:,"
    for _ in range(300):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 400)))
        text = text.strip(" ")
        value, _ = roundtrip(text, indent=rnd.choice([2, 4]))
        assert value == text, repr(text)


def test_break_happens_at_best_width():
    text = "x" * (BEST_WIDTH - len("  _serializedGraph: '")) + " tail"
    _, lines = roundtrip(text)
    assert lines[1].endswith("x")
    assert lines[2] == "    tail'"


def test_fsjson_preserves_number_text():
    src = '{"a":1E-05,"b":0.30000001192092896,"c":320.0,"d":-7,"e":"q\\"\\r\\n","f":[true,false,null]}'
    assert fsjson.dumps(fsjson.loads(src)) == src


def test_fsjson_new_floats():
    assert fsjson.dumps({"x": 1.0, "y": 0.25, "z": 1e-05}) == '{"x":1.0,"y":0.25,"z":1E-05}'
