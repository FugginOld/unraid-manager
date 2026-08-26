# unraid-link
Link multiple Unraid arrays

## Development prerequisites

- Python 3.11+ (the daemon targets 3.11, stdlib only — no pip installs).
- PHP 8.2 with the `pdo_sqlite` and `sqlite3` extensions enabled.
- Verify: `python -m unittest discover -s tests/python` and `bash tests/php/run.sh`.
