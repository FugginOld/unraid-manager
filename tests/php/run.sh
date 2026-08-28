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

# Zero-dependency proofs of behaviour no PHP source-text check can see:
#   live_singleton.mjs - live.js's singleton/refcount/mount-fetch behaviour
#   node_card.mjs      - NodeCard.vue's rendered output (null-vs-zero,
#                        empty-array-vs-0%, unknown-vs-failed)
#   views.mjs          - Disks.vue and Drift.vue's rendered output (an array
#                        slot with no disk behind it, 0-vs-unknown errors,
#                        never-polled vs failed-poll, absent vs unreported)
# Guarded the way build.sh guards npm: skipped where node is absent, mandatory
# in CI (ubuntu-latest ships node, which the "Lint JS" step above relies on).
if command -v node >/dev/null; then
    for t in tests/js/live_singleton.mjs tests/js/node_card.mjs tests/js/views.mjs; do
        echo "--- $t"
        out=$(node "$t" 2>&1)
        code=$?
        echo "$out"
        if [ "$code" -ne 0 ]; then
            fails=$((fails + 1))
        elif ! grep -q ': all pass$' <<< "$out"; then
            echo "!!! $t exited 0 without ever printing 'all pass' - treating as FAILED"
            fails=$((fails + 1))
        fi
    done
else
    echo "!!! node not on PATH - skipping tests/js/*.mjs (mandatory in CI)"
fi

if [ "$fails" -ne 0 ]; then
    echo "php suite: $fails file(s) FAILED"
    exit 1
fi
echo "php suite: all pass"
