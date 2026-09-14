import pytest

from support_db_staff import _coerce_staff_salary


@pytest.mark.parametrize("value", ["not-a-number"])
def test_staff_salary_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        _coerce_staff_salary(value)


def test_staff_salary_clamps_values_to_supported_bounds():
    assert _coerce_staff_salary(-1) == 0
    assert _coerce_staff_salary(100_000_001) == 100_000_000


def test_staff_salary_accepts_values_within_bounds():
    assert _coerce_staff_salary(0) == 0
    assert _coerce_staff_salary(100_000_000) == 100_000_000