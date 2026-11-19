import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Integration tests sign up several users from one client address; start each test with a clean limiter."""
    try:
        from app.security import limiter
    except Exception:  # settings may not be importable in pure unit tests
        yield
        return
    limiter.hits.clear()
    yield
