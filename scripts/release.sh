#!/usr/bin/env bash
# Cut a release: bump the version, date the CHANGELOG, commit, tag, push.
#
#   ./scripts/release.sh 1.2.0          # or 1.2.0-rc.1 for a pre-release
#   ./scripts/release.sh 1.2.0 --dry    # do everything but push
#
# Pushing the tag starts .github/workflows/release.yml, which tests, builds,
# publishes to PyPI and makes the GitHub release. The version lives in
# package.json alone; hatch reads it from there.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?usage: $0 <version> [--dry]}"
DRY="${2:-}"
TAG="v$VERSION"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?$ ]] \
  || { echo "error: '$VERSION' is not X.Y.Z or X.Y.Z-rc.N" >&2; exit 1; }
[ "$(git branch --show-current)" = main ] \
  || { echo "error: release from main" >&2; exit 1; }
git diff --quiet -- . ':!coverage' && git diff --cached --quiet \
  || { echo "error: uncommitted changes" >&2; exit 1; }
git fetch -q origin main --tags
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
  || { echo "error: main is not in step with origin/main" >&2; exit 1; }
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: $TAG already exists" >&2; exit 1
fi

# The CHANGELOG heading for this version, "(unreleased)" or not yet dated.
BASE="${VERSION%%-*}"
grep -qE "^## ($VERSION|$BASE)( |$)" CHANGELOG.md \
  || { echo "error: CHANGELOG.md has no '## $VERSION' (or '## $BASE') section" >&2; exit 1; }

node -e '
  const fs = require("fs");
  const p = JSON.parse(fs.readFileSync("package.json"));
  p.version = process.argv[1];
  fs.writeFileSync("package.json", JSON.stringify(p, null, 4) + "\n");
' "$VERSION"

TODAY="$(date +%Y-%m-%d)"
# A pre-release keeps the section open for the final release to date.
if [ "$VERSION" = "$BASE" ]; then
  sed -i.bak -E "s/^## $BASE( \(unreleased\))?$/## $BASE ($TODAY)/" CHANGELOG.md
  rm CHANGELOG.md.bak
fi

git add package.json CHANGELOG.md
git commit -q -m "Release $VERSION"
git tag -a "$TAG" -m "Release $VERSION"
echo "committed and tagged $TAG"

if [ "$DRY" = "--dry" ]; then
  echo "dry run: not pushed. To undo: git tag -d $TAG && git reset --hard HEAD~1"
  exit 0
fi
git push -q origin main "$TAG"
echo "pushed. Watch: gh run watch \$(gh run list -w Release -L1 --json databaseId -q '.[0].databaseId')"
