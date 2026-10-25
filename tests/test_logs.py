"""JSON log format matches workgraph's keys."""

from __future__ import annotations

import json
import logging

import pytest
from logs import JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hi %s", ("x",), None)
    record.__dict__.update(extra)
    return record


def test_record_is_one_json_line_with_workgraph_keys() -> None:
    line = JsonFormatter().format(_record())
    assert "\n" not in line
    assert set(json.loads(line)) == {"time", "level", "logger", "message"}
    assert json.loads(line)["message"] == "hi x"


def test_exc_with_exc_info() -> None:
    err = ValueError("boom")
    record = _record()
    record.exc_info = (ValueError, err, None)
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc"]


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ({"namespace": "ns", "name": "n"}, "ns/n"),
        ({"namespace": None, "name": "n"}, "n"),
    ],
)
def test_object_from_k8s_ref(ref: dict[str, str | None], expected: str) -> None:
    line = JsonFormatter().format(_record(k8s_ref=ref))
    assert json.loads(line)["object"] == expected


def test_configure_logging_leaves_one_json_handler() -> None:
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        root.addHandler(logging.NullHandler())
        configure_logging()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers[:] = saved
