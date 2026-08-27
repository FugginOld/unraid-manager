# Contributing

Read [ARCHITECTURE.md](ARCHITECTURE.md) first, and
[docs/review-policy.md](docs/review-policy.md) before reviewing anything or
acting on a "simplify this" finding.

## Running the tests

Both suites must pass before anything is committed.

```bash
python -m unittest discover -s tests/python        # 210 tests
bash tests/php/run.sh                              # 9 suites
```

Neither touches a network or a live Unraid box, by rule. Fixtures only.

**On this Windows dev machine PHP is installed but not on the bash `PATH`:**

```bash
export PATH="/c/Users/Joe/AppData/Local/Microsoft/WinGet/Packages/PHP.PHP.8.2_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"
```

`pdo_sqlite` and `sqlite3` are both present in the CLI. Note that **php-fpm on
Unraid has only `sqlite3`** — see the trap below.

## Version floors, and why CI is the authority

| | Dev machine | Target | Authority |
| --- | --- | --- | --- |
| Python | 3.13 | **3.11** (Unraid 7.3.2 base image) | CI pins 3.11 |
| PHP | 8.2 CLI | 8.2 **fpm-fcgi** | The box |

Use no syntax or stdlib API newer than 3.11. A 3.12+ idiom passes locally and
fails in CI, which is the point of pinning it there.

The PHP row is the more dangerous one. The CLI and php-fpm are **different
platforms with different extension sets**, and the suite runs on the CLI. This
already cost a full evening: every database read in the PHP layer failed on the
target because `pdo_sqlite` exists in the CLI and not in php-fpm, while CI stayed
green. The read layer now uses the `SQLite3` class. When you add a PHP dependency
on anything, ask what php-fpm actually has — not what your CLI reports.

## The daemon is stdlib-only

No pip, no vendored module, in the daemon or its tests. CI fails the build if an
import of `requests`, `yaml`, `urllib3`, `aiohttp` or `pydantic` appears under
`daemon/`. There is no package manager on the target worth relying on and a
plugin that needs one is a plugin that breaks on someone else's server.

## How changes get made

1. **Read the repo's own docs** and `docs/verification/tier0-coverage.md`. That
   file is binding and overrides any design document that disagrees with it.
2. **Diagnose before implementing.** A report names a symptom; find the shared
   function every caller routes through.
3. **Write the plan to a file** for anything non-trivial, then execute against
   it. Plans survive compaction; context does not.
4. **TDD**: reproduce, failing test, minimal fix, verify. Every fix in this repo
   landed with a test that would catch its return — keep it that way.
5. **Run both suites** before declaring done.

## Testing rules that are not negotiable

- **No test may require a live box.** Capture a fixture instead
  (`scripts/capture_fixtures.py`), and never commit a key.
- **Fixtures are evidence, not editable data.** When a test and a captured
  response disagree, the fixture is right and the expectation is wrong. Several
  P0 tests were written against a design document's invented JSON and had to be
  corrected to the real captures.
- **Invariant tests stay.** No raw serial in an API-bound payload, no secret in
  a log line, no write path resolving under `/boot` outside the config dir.
- **A guarded no-op must still succeed.** `[ -x foo ] && foo stop` exits 1 when
  the file is absent, which aborted every fresh install until it was found on a
  real box.

## Secrets

No API key is ever committed, logged, returned in a response, put in a URL, or
pasted into a transcript. Three layers enforce it: `.gitignore`, the fixture
scan in `test_fixtures.py`, and the `secrets` CI job. Keys live on flash at
`0600`, one file per node, and the daemon reads them itself — the PHP layer never
handles key material for a node that is already enrolled.

If a key is ever exposed, the recovery is `unraid-api apikey --delete` on that
box and a fresh one.

## Commits and branches

- Work on `dev`. **Nothing goes to `main` until the project is complete** — the
  `.plg` on `main` is what Unraid clients poll for updates, so a push there is a
  release whether you meant it or not.
- Conventional-ish subjects: `feat(daemon):`, `fix(api):`, `docs:`, `ci:`.
- Say what broke and how it was found, not just what changed. The commit log
  from the P0 live trial is the most useful documentation in this repo precisely
  because each message names the box and the symptom.

## Releasing

Tag-driven, from `main`, once the project is ready for it:

1. Add a `###YYYY.MM.DD###` block to `<CHANGES>` in `unraid-manager.plg`.
2. Commit, push, then `git tag YYYY.MM.DD && git push --tags`.

`release.yml` runs both suites, builds the `.txz`, patches `version`, `md5` and
`pkgURL` in the `.plg` on `main`, re-checks the patched file, and publishes. No
CHANGES block, no release — that is the forcing function for the changelog
Unraid shows in its plugin manager.

To test a build on a box without cutting a release, see the local-install
recipe in [HOWTO.md](HOWTO.md).
