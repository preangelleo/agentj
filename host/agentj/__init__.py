"""Agent J host (`agentj`). The version below is the one source of truth (pyproject reads it; `agentj --version`;
jarvis_host/__init__.py repeats it for hosts ≤ 0.9, whose update check reads that file by path — a test keeps them equal)."""
__version__ = "0.10.0a1"
DIST = "agentj"
