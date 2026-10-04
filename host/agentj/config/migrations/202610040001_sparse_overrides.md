# 202610040001 — sparse override baseline

`config_migrations.py` validates the initial JSON5 format without rewriting user bytes.
Initialization runs the immutable package migration list; `config migrate --pending` lists unfinished IDs.
`config migrate` executes offline under the preferences lock. Successful timestamp markers are written last.
Changed files use bounded history and last-good; transform, validation, write or marker failure restores the prior files.
Future transformations must be idempotent and use the structural editor to preserve owner comments.
A package upgrade merges new defaults independently of user overrides; it never copies defaults onto the user file.
