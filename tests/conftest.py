import os
import sys
import tempfile

# Must be set before datadragon is imported: it creates uploads/ and output/ at import time.
_DATA_DIR = tempfile.mkdtemp(prefix="datadragon_tests_")
os.environ["DATADRAGON_DATA_DIR"] = _DATA_DIR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import datadragon  # noqa: E402


@pytest.fixture(scope="session")
def dd():
    return datadragon


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    datadragon.rate_limit_store.clear()
    yield
    datadragon.rate_limit_store.clear()


@pytest.fixture
def client():
    datadragon.app.config["TESTING"] = True
    return datadragon.app.test_client()
