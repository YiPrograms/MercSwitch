class MercSwitchError(Exception):
    """Base exception for mercswitch."""


class AuthenticationError(MercSwitchError):
    pass


class UnsupportedDeviceError(MercSwitchError):
    pass


class ParseError(MercSwitchError):
    pass


class ValidationError(MercSwitchError):
    pass


class DriftError(MercSwitchError):
    pass


class ApplyError(MercSwitchError):
    pass
