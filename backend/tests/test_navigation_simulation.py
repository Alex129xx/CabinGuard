import pytest

from app.main import position_on_route


def test_position_on_route_interpolates_between_points():
    longitude, latitude = position_on_route([[121.0, 31.0], [122.0, 31.0]], 0.25)
    assert longitude == pytest.approx(121.25)
    assert latitude == pytest.approx(31.0)


def test_position_on_route_preserves_endpoints():
    assert position_on_route([[121.0, 31.0], [122.0, 31.0]], 0) == pytest.approx((121.0, 31.0))
    assert position_on_route([[121.0, 31.0], [122.0, 31.0]], 1) == pytest.approx((122.0, 31.0))
