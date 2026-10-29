import pytest

from verikyb.api.auth import permission_denied_message


@pytest.mark.parametrize(
    "role, expected",
    [
        ("analyst", "an analyst cannot do this"),
        ("reviewer", "a reviewer cannot do this"),
        ("admin", "an admin cannot do this"),
    ],
)
def test_permission_denied_message(role, expected):
    assert permission_denied_message(role) == expected
