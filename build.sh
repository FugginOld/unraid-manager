#!/bin/bash
# Builds unraid-manager.txz from source/.
# Run on Linux (or on the Unraid server) before cutting a GitHub release.
#
# Output: releases/unraid-manager.txz
#
# Usage:
#   bash build.sh [version]
#   bash build.sh 2026.08.25
#
# Unlike the HBAviewer build this one downloads nothing: every byte shipped is
# in this repo. Keep it that way — a fetch here is a supply-chain surface on a
# package that unpacks as root on someone else's server.

set -e

VERSION="${1:-$(date +%Y.%m.%d)}"
OUTPUT="releases/unraid-manager.txz"
PLUGDIR="source/usr/local/emhttp/plugins/unraid-manager"

echo "==> Unraid-Manager build  (version: $VERSION)"

# Refuse to package a key. Enrollment keys live on flash at 0600 and are
# supplied by the operator at run time; one committed by accident would ship to
# every install. Covers loose *.key/*.pem files and anything under a keys/
# directory (.gitignore's other reserved spot for secrets).
if find source \( -name '*.key' -o -name '*.pem' -o -path '*/keys/*' \) -print -quit | grep -q .; then
    echo "ERROR: a .key file is inside source/. Refusing to package a secret."
    exit 1
fi

test -d "$PLUGDIR" || { echo "ERROR: $PLUGDIR missing"; exit 1; }
test -f "$PLUGDIR/scripts/rc.unraid-manager" || echo "WARN: rc script not present yet"

# The pane is a Vue bundle, built here rather than committed, so what ships is
# what the reviewed source produces. Refused outright rather than packaging a
# stale ui/ that happens to be lying around.
if [ -d frontend ]; then
    command -v npm >/dev/null || {
        echo "ERROR: npm not found and frontend/ exists."
        echo "  The pane cannot be built. Install node, or build on a machine that has it."
        exit 1
    }
    echo "--> Building the frontend..."
    ( cd frontend && npm ci --silent && npm run build ) || {
        echo "ERROR: the frontend build failed. Refusing to package a broken pane."
        exit 1
    }
fi

# Bytecode is not source. Running the suite leaves __pycache__ under source/,
# and everything under source/ ships - so a build after a local test run puts
# this machine's 3.13 .pyc files on a 3.11 box. Harmless (Python ignores a
# mismatched magic number) and still wrong: it is not what was reviewed.
find source -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find source -name '*.pyc' -delete 2>/dev/null

mkdir -p releases
echo "--> Building $OUTPUT..."
cd source
if command -v makepkg &>/dev/null; then
    makepkg -l y -c n "../$OUTPUT"
else
    # makepkg is Slackware-only; CI and dev machines get the plain tar the
    # HBAviewer releases have always actually shipped.
    tar --owner=root --group=root -cJf "../$OUTPUT" .
fi
cd ..

MD5=$(md5sum "$OUTPUT" | awk '{print $1}')
echo "--> MD5: $MD5"
echo ""
echo "Done: $OUTPUT"
echo ""
echo "Next steps (release.yml does all of this for a pushed tag):"
echo "  1. <!ENTITY md5     \"$MD5\"> in unraid-manager.plg"
echo "  2. <!ENTITY version \"$VERSION\"> in unraid-manager.plg"
echo "  3. git tag $VERSION && git push --tags"
