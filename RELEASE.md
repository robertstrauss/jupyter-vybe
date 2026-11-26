# Releasing jupyter_vybe

A release is a tag. Pushing `vX.Y.Z` runs
[`.github/workflows/release.yml`](.github/workflows/release.yml), which:

1. checks that the tag matches `package.json`'s version,
2. runs the lint, jest, invariant gates and pytest,
3. builds the sdist and wheel and checks them with `twine check --strict`,
4. installs the wheel in a clean virtualenv and checks that both the lab
   extension and the server extension load,
5. publishes to [PyPI](https://pypi.org/project/jupyter-vybe/) by trusted
   publishing (no token is stored anywhere), and
6. makes a GitHub release with the wheel and sdist attached and that
   version's CHANGELOG section as the notes.

The version lives only in `package.json`. Hatch reads it from there
(`hatch-nodejs-version`), so `1.2.0-rc.1` there becomes `1.2.0rc1` on PyPI.

## Cutting a release

1. Under `## X.Y.Z (unreleased)` in `CHANGELOG.md`, write what changed.
2. Make sure CI on `main` is green.
3. Run:

   ```bash
   ./scripts/release.sh 1.2.0 --dry   # commit and tag locally, push nothing
   git tag -d v1.2.0 && git reset --hard HEAD~1   # undo the dry run
   ./scripts/release.sh 1.2.0         # the real thing
   ```

   The script refuses to run unless you are on `main`, have no uncommitted
   changes, are level with `origin/main`, and have a CHANGELOG section for
   the version. It sets the version, replaces `(unreleased)` with today's
   date, commits `Release 1.2.0`, tags `v1.2.0` and pushes both.

4. Watch it run: `gh run watch $(gh run list -w Release -L1 --json databaseId -q '.[0].databaseId')`.

A pre-release (`./scripts/release.sh 1.2.0-rc.1`) is published to PyPI as
`1.2.0rc1`. pip skips it unless asked (`pip install --pre jupyter-vybe`). It
is marked as a pre-release on GitHub, and the CHANGELOG section stays
undated for the final release.

If a release fails before the PyPI step, fix the problem, delete the tag
(`git push origin :v1.2.0 && git tag -d v1.2.0`) and run the script again
with the same version. Once a version is on PyPI it can never be uploaded
again, so a broken release needs a new patch version.

## One-time setup

These are already done unless marked otherwise.

- **PyPI trusted publisher** (needs the maintainer's PyPI account). At
  <https://pypi.org/manage/account/publishing/>, add a _pending publisher_:

  | Field             | Value           |
  | ----------------- | --------------- |
  | PyPI project name | `jupyter-vybe`  |
  | Owner             | `robertstrauss` |
  | Repository name   | `jupyter-vybe`  |
  | Workflow name     | `release.yml`   |
  | Environment name  | `pypi`          |

  The first successful release creates the project and turns the pending
  publisher into a normal one.

- **The `pypi` GitHub environment**, which the publish job runs in. Under
  Settings → Environments → `pypi` you can add required reviewers, so that
  a pushed tag waits for a click before anything reaches PyPI.

## npm

The npm package is not published. JupyterLab 4 installs the extension
prebuilt from the Python wheel, so npm is only needed by someone building
another extension on top of this one. To publish it as well, add a job to
`release.yml` that runs `npm publish --provenance --access public` with
`id-token: write`, after setting up npm trusted publishing for the package.

## conda-forge

Not on conda-forge yet. To add it, see
<https://conda-forge.org/docs/maintainer/adding_pkgs.html>. Once there is a
feedstock, its bot opens a PR for each new PyPI release.
