

def test_websockets_plain_probe_is_info_with_a_fixed_message():
    """V4-LG-1: a plain TCP probe of the link port is not an ERROR."""
    import logging
    from jarves import logs
    try:
        raise ValueError("did not receive a valid HTTP request")
    except ValueError:
        import sys
        r = logging.LogRecord("websockets.server", logging.ERROR, "x.py", 1, "opening handshake failed", None, sys.exc_info())
    n = logs._lib_record(r)
    assert n.levelno == logging.INFO and n.exc_info is None
    assert n.getMessage() == "a client connected to the link port without a WebSocket handshake"
    real = logging.LogRecord("websockets.server", logging.ERROR, "x.py", 1, "server crashed", None, None)
    assert logs._lib_record(real).levelno == logging.ERROR
