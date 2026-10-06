"""Final-only silence contract shared by relay and Agent J (P68)."""
MARKER = "〔不回群〕"

def is_silent(text):
    return isinstance(text, str) and text.strip() == MARKER
