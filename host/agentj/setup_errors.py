"""Controlled setup failures, shared by finite graph and native effects."""
class RuntimeErrorCode(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


