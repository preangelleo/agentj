"""Outbound iLink filename validation shared by file admission and transport."""


def file_name(name):
    """Return the original basename unchanged, or refuse it with the protocol error."""
    if (not isinstance(name, str) or not name.strip() or name in ('.', '..') or '/' in name or '\\' in name
            or any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 or 0xd800 <= ord(c) <= 0xdfff for c in name)):
        # An upgrading process can still hold the predecessor protocol module.
        from .wechat_protocol import Failure

        raise Failure('invalid_file_name')
    return name
