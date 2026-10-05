"""Compatibility package for one version cycle: the host was `jarvis_host` up to 0.9 and is `agentj` now.

Hosts ≤ 0.9 check for updates by reading this file by path (host/jarvis_host/__init__.py in the public repo) and parsing the
line below as text, so it stays here, literal, equal to agentj.__version__ (tests/test_rename.py). A service unit written by
0.9 may still run `python -m jarvis_host.cli serve`: jarvis_host.cli hands over to the agentj CLI.
"""
__version__ = "0.15.1a1"
DIST = "agentj"
