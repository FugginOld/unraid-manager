# Fixtures

`seed/` — hand-built from the response shapes recorded in
`docs/verification/tier0-coverage.md` and the field names in
`docs/verification/graphql-schema-raven.json`. They exist so no task in the P0
plan is blocked on a live box, and they are the baseline the parsers were
written against.

`raven/`, `golem/` — real captures, written by `scripts/capture_fixtures.py`.
Not committed until captured; when they land they sit beside the seeds, and the
parser tests run against both.

**No fixture may contain an API key.** `tests/python/test_fixtures.py` fails the
suite on any 40+ character token-shaped string in this directory. Serial numbers
in captures are masked length-preservingly by the capture script.

To capture (operator, on the dev machine, key supplied at run time):

```
set UNRAID_API_KEY=<paste>            # PowerShell: $env:UNRAID_API_KEY="<paste>"
python scripts/capture_fixtures.py --host 192.168.2.19  --port 29220 --label raven
python scripts/capture_fixtures.py --host 192.168.2.248 --port 15137 --label golem
```
