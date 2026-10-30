SECRET_RE = [re.compile(p) for p in (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}", r"\bgithub_pat_[A-Za-z0-9_]{40,}",
    r"\bglpat-[A-Za-z0-9_-]{20,}", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", r"\b(?:sk|rk|whsec)_live_[A-Za-z0-9]{16,}",
    r"\bAIza[0-9A-Za-z_-]{35}", r"\bsk-ant-[A-Za-z0-9_-]{20,}", r"\bsk-or-v1-[A-Za-z0-9]{40,}", r"\bsk-(?:proj-|svcacct-|admin-)[A-Za-z0-9_-]{20,}",
    r"\br8_[A-Za-z0-9]{30,}", r"\bhf_[A-Za-z0-9]{30,}", r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b", r"\baj[ahijmrst]_[A-Za-z0-9_-]{43}\b",
    r"\bAJV-[A-Z0-9]{4}-[A-Z0-9]{4}\b", r"\bAJUP-[A-Z0-9]{5}(?:-[A-Z0-9]{5}){3}\b", r"\bAJ-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}\b",
    r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"$<{]{6,}@")]
