# Deployment guide (maintainers)

This is maintainer-only documentation for publishing Aura Connector for the first time and
for cutting subsequent releases. It assumes you have admin rights on the GitHub repository
and an account on PyPI.

The package uses PyPI Trusted Publishing (OIDC), so no API tokens are stored in GitHub.

## 1. First-time GitHub setup

Create the repository on GitHub (named `aura-connector`), then point the local clone at
it and push:

```bash
git status
git branch -M main
git remote -v
git remote add origin https://github.com/Ohswedd/aura-connector
```

The `Ohswedd/aura-connector` references in `README.md`, `CHANGELOG.md`, and
`pyproject.toml` already point at the live repository.

## 2. Repository settings

- Set the default branch to `main`.
- Enable Issues.
- Under Settings, Code security: enable private vulnerability reporting (used by
  `SECURITY.md`).
- Dependabot is configured by `.github/dependabot.yml` and turns on once the repo is
  pushed.

## 3. Branch protection (suggested)

For `main`:

- Require a pull request before merging.
- Require status checks to pass: the `CI` jobs and the `Native` jobs.
- Require branches to be up to date before merging.
- Do not allow force pushes.

## 4. PyPI Trusted Publishing setup

On PyPI, create a pending publisher before the first release so the OIDC handshake works
on the first run:

1. Sign in to https://pypi.org and go to your account, then "Publishing".
2. Add a new pending trusted publisher with:
   - PyPI project name: `aura-connector`
   - Owner: `Ohswedd`
   - Repository: `aura-connector`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. In GitHub, create an Environment named `pypi` (Settings, Environments). No secrets are
   required.

The `publish.yml` workflow requests `id-token: write` and uses
`pypa/gh-action-pypi-publish`, so no token is stored anywhere.

## 5. TestPyPI (optional dry run)

To rehearse against TestPyPI first, register an identical pending publisher on
https://test.pypi.org and temporarily add `repository-url: https://test.pypi.org/legacy/`
to the publish step in `.github/workflows/publish.yml`. Remove it before publishing to the
real index.

## 6. GitHub secrets needed

None for publishing. Trusted Publishing replaces long-lived API tokens. The only
requirement is the `pypi` environment described above.

## 7. Verify package metadata

```bash
python -m pip install -e ".[dev]"
python -m build
python -m twine check dist/*
```

`twine check` validates the rendered README and core metadata.

## 8. Run the CI gate locally

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -vv
python -m compileall src tests examples
python -m build
```

## 9. Build pure-Python artifacts

```bash
python -m build           # writes sdist and wheel to dist/
```

## 10. Build the native extension locally

Requires a stable Rust toolchain and maturin.

```bash
python -m pip install -e ".[dev,native]"
maturin build --manifest-path crates/aura_native/Cargo.toml --release
python -m pip install crates/aura_native/target/wheels/*.whl
aura doctor               # native_acceleration: available
```

## 11. Run native tests

```bash
python -m pytest tests/unit/test_native_adapter.py tests/native/test_native_parity.py -vv
python benchmarks/bench_native_acceleration.py
```

If a Rust toolchain is not available on your machine, the native build cannot be verified
locally. The pure-Python path remains fully functional and is what the published wheel
provides by default. Native CI runs on Linux and macOS through `.github/workflows/native.yml`.

## 12. Create the first release

1. Confirm the version in `pyproject.toml` and `src/aura/__init__.py` matches the tag you
   are about to cut (both are `0.1.0` for the first release).
2. Move the `Unreleased` notes in `CHANGELOG.md` under a dated `0.1.0` heading.
3. Commit, tag, and push:

```bash
git commit -m "Release Aura Connector v0.1.0"
git tag v0.1.0
git push origin main --tags
```

## 13. Publish to PyPI via GitHub release

1. On GitHub, draft a new Release from the `v0.1.0` tag.
2. Paste the changelog entry as the release notes.
3. Publish the release.

Publishing the release triggers `.github/workflows/publish.yml`, which builds the sdist and
wheel, runs `twine check`, and uploads to PyPI through Trusted Publishing.

## 14. Recover from a failed release

- PyPI does not allow re-uploading the same version. If a publish fails after the version
  was already uploaded, bump to the next patch version and release again.
- If the publish failed before upload (build or check error), fix the issue on `main`,
  delete the GitHub release and the tag, then recreate them:

```bash
git tag -d v0.1.0
git push origin :refs/tags/v0.1.0
```

- You can re-run the failed `publish.yml` run from the Actions tab once the cause is fixed,
  as long as the version was not already uploaded.

## 15. Bump the version

For each release, update the version in two places and keep them in sync:

- `pyproject.toml` under `[project] version`
- `src/aura/__init__.py` `__version__`

Then update `CHANGELOG.md`, commit, tag `vX.Y.Z`, and push.

## 16. Avoid co-author metadata

Keep release commits free of `Co-authored-by` trailers unless you intend them. Check and
clean local git configuration:

```bash
git config --get commit.template || true
git config --get-all trailer.co-authored-by || true
git log -1 --pretty=%B
```

If a local commit template injects trailers, point it at an empty template or unset it:

```bash
git config commit.template ""
git config --unset-all trailer.co-authored-by || true
```

## 17. Exact command sequence

```bash
git status
git branch -M main
git remote -v
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -vv
python -m build
twine check dist/*
git tag v0.1.0
git push origin main --tags
```

Then draft and publish the GitHub release to trigger publishing.
