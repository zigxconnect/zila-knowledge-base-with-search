import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def no_real_page_fetching(monkeypatch):
    """Tests never touch the network. Page-image tests switch this back on explicitly."""
    monkeypatch.setattr(settings, "digest_fetch_page_images", False)
    monkeypatch.setattr(settings, "mock_mode", False)
