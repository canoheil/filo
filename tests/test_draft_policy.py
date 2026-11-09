import pytest
from pydantic import ValidationError
from core.config import Settings


def test_retention_policy_validation():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DRAFT_TTL_MINUTES=60, DRAFT_ERROR_RETENTION_HOURS=1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DRAFT_TTL_MINUTES=0)
    policy = Settings(_env_file=None)
    assert policy.DRAFT_TTL_MINUTES == 60
    assert policy.DRAFT_ERROR_RETENTION_HOURS == 24
