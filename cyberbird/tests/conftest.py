"""Shared fixtures. The scan output is generated, not committed, so scan once."""
import json

import pytest

from cyberbird.reactive import scan
from cyberbird.reactive.config import CONFIG


@pytest.fixture(scope="session")
def scanned():
    """Scan the fixture once per session: writes runs/scans/bandit.json and runs/findings.json."""
    assert scan.main([]) == 0
    return json.loads(CONFIG.findings.read_text())
