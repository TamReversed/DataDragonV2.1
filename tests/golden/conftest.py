import pytest

import snapshot


@pytest.fixture
def golden(request):
    def _check(name, actual):
        snapshot.check(request.config, name, actual)
    return _check
