"""Process-local guards must not leak admission history between tests."""
import pytest
from services.ai_guard import AIGuard
from handlers import secretary, menu


@pytest.fixture(autouse=True)
def isolated_ai_guard(monkeypatch):
    guard = AIGuard(clock=lambda: 0.0)
    monkeypatch.setattr(secretary, 'ai_guard', guard)
    monkeypatch.setattr(menu, 'ai_guard', guard)
    return guard
