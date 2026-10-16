# Contributing to Agent J

Thank you for helping. Bug reports, fixes, documentation improvements and new tests are all welcome.

By taking part you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## Security issues

Do **not** report vulnerabilities in a public issue. Follow [SECURITY.md](SECURITY.md) instead.

## Reporting a bug

Open a [GitHub issue](https://github.com/preangelleo/agentj/issues) with:

- what you did, what you expected, and what happened instead;
- your platform (macOS, Linux, or Windows with WSL2), the output of `agentj --version`, and which agent you use
  (Claude Code, Codex or OpenCode);
- relevant logs or `agentj doctor` output, **with every token, key, passphrase, pairing link and personal path
  removed**.

## Proposing a change

1. For anything larger than a small fix, open an issue first so we can agree on the approach.
2. Fork the repository and create a branch.
3. Make your change, with tests that cover it.
4. Run the test suites below; they must pass.
5. Open a pull request that explains what changed and why.

## Development setup

You need Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) and Node.js. On Linux, the fence tests also need
bubblewrap.

```bash
node --test protocol/test/*.test.mjs                                   # Noise against the official vectors
(cd host && uv sync && .venv/bin/python -m unittest discover -s tests)  # host CLI (Linux: needs bubblewrap for the fence tests)
node web/build.mjs && node --test web/test/*.test.mjs                  # web client static rules + Worker
```

If you change anything under `web/public/` or `protocol/`, run `node web/build.mjs` and commit its output: the web
client's published hash is computed from those files.

## Code style

Match the surrounding code. Keep changes small and focused, and do not reformat code you did not otherwise touch.
Never commit secrets, `.env` files or local state.

## How changes reach this repository

This public repository is a mirror, exported from the maintainers' tree for every release. We do not merge pull
requests here directly: an accepted pull request is applied upstream and appears in the next export. Your authorship
is credited in the commit message or the release notes.

## License of contributions

Agent J is licensed under the [Apache License 2.0](LICENSE). Unless you state otherwise, any contribution you submit
is licensed under the same terms, as described in section 5 of the License. There is no Contributor License
Agreement to sign.

The "Agent J" name and logo are covered by [TRADEMARKS.md](TRADEMARKS.md), not by the code license.
