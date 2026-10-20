"""Guard: the test suite can never open a real browser (a wizard test once opened t.me/BotFather on the desktop)."""
import webbrowser


def test_webbrowser_is_faked_in_tests(_no_real_browser):
    assert webbrowser.open("https://t.me/BotFather") is True
    assert _no_real_browser == ["https://t.me/BotFather"]


def test_connect_default_opener_never_reaches_the_desktop(_no_real_browser):
    from jarves import connect
    import inspect
    src = inspect.getsource(connect)
    assert "webbrowser.open" in src                      # the production default stays
    assert connect.webbrowser.open is webbrowser.open    # ...but inside tests it is the recorded fake
    connect.webbrowser.open("https://t.me/BotFather")
    assert "https://t.me/BotFather" in _no_real_browser
