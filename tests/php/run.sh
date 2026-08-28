#!/bin/bash
# Runs every PHP test in this directory. One non-zero exit fails the suite.
#   bash tests/php/run.sh
set -u
cd "$(dirname "$0")/../.." || exit 1
command -v php >/dev/null || { echo "php not on PATH"; exit 2; }
fails=0
for t in tests/php/*_test.php; do
    [ -e "$t" ] || continue
    echo "--- $t"
    out=$(php -f "$t" 2>&1)
    code=$?
    echo "$out"
    if [ "$code" -ne 0 ]; then
        fails=$((fails + 1))
    elif ! grep -q ': all pass$' <<< "$out"; then
        echo "!!! $t exited 0 without ever printing 'all pass' - treating as FAILED"
        fails=$((fails + 1))
    elif grep -qE '^(PHP )?(Warning|Notice|Deprecated|Fatal error|Parse error):' <<< "$out"; then
        # A guard whose only symptom is a diagnostic is otherwise unpinnable:
        # remove it, the output is identical, the file still prints 'all pass'
        # and exits 0. That is how an unguarded foreach shipped once already.
        echo "!!! $t printed a PHP diagnostic - treating as FAILED"
        fails=$((fails + 1))
    fi
done

# Zero-dependency proof of live.js's singleton/mount-fetch behaviour - not
# expressible as a PHP source-text check. Guarded the way build.sh guards
# npm: skipped where node is absent, mandatory in CI (ubuntu-latest ships
# node, which the "Lint JS" step above already relies on).
if command -v node >/dev/null; then
    echo "--- tests/js/live_singleton.mjs"
    out=$(node tests/js/live_singleton.mjs 2>&1)
    code=$?
    echo "$out"
    if [ "$code" -ne 0 ]; then
        fails=$((fails + 1))
    elif ! grep -q ': all pass$' <<< "$out"; then
        echo "!!! tests/js/live_singleton.mjs exited 0 without ever printing 'all pass' - treating as FAILED"
        fails=$((fails + 1))
    fi
else
    echo "!!! node not on PATH - skipping tests/js/live_singleton.mjs (mandatory in CI)"
fi

if [ "$fails" -ne 0 ]; then
    echo "php suite: $fails file(s) FAILED"
    exit 1
fi
echo "php suite: all pass"
