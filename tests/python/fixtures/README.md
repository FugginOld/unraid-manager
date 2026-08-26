# Fixtures

`seed/` — hand-built from the response shapes recorded in
`docs/verification/tier0-coverage.md` and the field names in
`docs/verification/graphql-schema-raven.json`. They exist so no task in the P0
plan is blocked on a live box, and they are the baseline the parsers were
written against.

`raven/`, `golem/` — real captures, written by `scripts/capture_fixtures.py`.
Not committed until captured; when they land they sit beside the seeds, and the
parser tests run against both.

**No fixture may contain an API key.** `tests/python/test_fixtures.py` recursively
walks this whole directory — not just `seed/` — and fails the suite on any
28+ character run of letters/digits/underscore. Real API keys are 64 lowercase-hex
characters, so they're always caught; this project's UUID node ids never are,
since a hyphen breaks a UUID into segments no longer than 12 characters. Serial
numbers in captures are masked length-preservingly by the capture script.

To capture (operator, on the dev machine, key supplied at run time):

PowerShell:

```
$env:UNRAID_API_KEY="<paste>"
python scripts/capture_fixtures.py --host 192.168.2.19  --port 29220 --label raven
python scripts/capture_fixtures.py --host 192.168.2.248 --port 15137 --label golem
```

Git Bash:

```
export UNRAID_API_KEY=<paste>
python scripts/capture_fixtures.py --host 192.168.2.19  --port 29220 --label raven
python scripts/capture_fixtures.py --host 192.168.2.248 --port 15137 --label golem
```
