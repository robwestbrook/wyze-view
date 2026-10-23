from wyze_view.application import preferred_backend


def test_backend_is_always_x11():
    assert preferred_backend("wayland") == "x11"
    assert preferred_backend("x11") == "x11"
    assert preferred_backend(None) == "x11"
