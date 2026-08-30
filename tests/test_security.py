from mercswitch.adapters.rpm_cgi import security_encode


def test_security_encode_is_deterministic_and_fixed_width():
    assert security_encode("dream14789632") == security_encode("dream14789632")
    assert len(security_encode("short")) == 15
    assert security_encode("one") != security_encode("two")
