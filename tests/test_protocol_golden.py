import json
from pathlib import Path

import pytest

from jupyter_vybe.protocol import parse


FIXTURE_DIR = Path(__file__).parent / "protocol"


def load_fixture(path: Path) -> tuple[str, list[dict]]:
    with path.open(newline="") as fixture:
        raw, expected = fixture.read().split("\n---EXPECTED---\n")
    return raw, [json.loads(line) for line in expected.splitlines() if line]


@pytest.mark.parametrize("path", sorted(FIXTURE_DIR.glob("*.txt")))
def test_protocol_golden(path: Path) -> None:
    raw, expected = load_fixture(path)
    assert [event.to_json() for event in parse(raw)] == expected
