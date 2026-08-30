import httpx

from mercswitch.cli import _error_message


def test_timeout_error_is_not_blank():
    request = httpx.Request("GET", "http://192.168.2.251/")

    message = _error_message(httpx.ReadTimeout("", request=request))

    assert message == (
        "ReadTimeout: timed out requesting GET http://192.168.2.251/; "
        "check container network access to the switch"
    )


def test_empty_error_uses_exception_type():
    assert _error_message(AssertionError()) == "AssertionError"
