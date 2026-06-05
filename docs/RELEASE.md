# Release guide (maintainers)

Maintainer documentation for cutting an Aura Connector release. The package uses
PyPI Trusted Publishing (OIDC), so no API tokens are stored in GitHub. The
current released version is `0.3.0`.

## Repository setup (one-time, already configured)

These are recorded for reference; they are already in place on the live
repository:

- Default branch `main`, Issues enabled, and GitHub private vulnerability
  reporting enabled (used by `SECURITY.md`).
- Dependabot via `.github/dependabot.yml`.
- Branch protection on `main`: require a pull request, require the CI and Native
  status checks to pass, require branches to be up to date before merging, and
  disallow force pushes.
- PyPI Trusted Publishing: a pending publisher for project `aura-connector`
  (owner `Ohswedd`, repository `aura-connector`, workflow `publish.yml`,
  environment `pypi`), and a matching GitHub Environment named `pypi`. No secrets
  are stored; `publish.yml` requests `id-token: write` and uses
  `pypa/gh-action-pypi-publish`.

To rehearse against TestPyPI, register an identical pending publisher on
`test.pypi.org` and temporarily add
`repository-url: https://test.pypi.org/legacy/` to the publish step, then remove
it before publishing to the real index.

## 1. Run the CI gate locally

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -vv
python -m compileall src tests examples
python -m build
```

## 2. Verify package metadata

```bash
python -m build           # writes sdist and wheel to dist/
python -m twine check dist/*
```

`twine check` validates the rendered README and core metadata.

## 3. Build and test the native extension (optional)

Requires a stable Rust toolchain and maturin.

```bash
python -m pip install -e ".[dev,native]"
maturin build --manifest-path crates/aura_native/Cargo.toml --release
python -m pip install crates/aura_native/target/wheels/*.whl
aura doctor               # native_acceleration: available
python -m pytest tests/unit/test_native_adapter.py tests/native/test_native_parity.py -vv
python benchmarks/bench_native_acceleration.py
```

If a Rust toolchain is not available locally, the native build cannot be verified
on your machine. The pure-Python path remains fully functional and is what the
published wheel provides by default; native CI runs on Linux and macOS through
`.github/workflows/native.yml`.

## 4. Bump the version

Update the version in two places and keep them in sync:

- `pyproject.toml` under `[project] version`
- `src/aura/__init__.py` `__version__`

Then move the `Unreleased` notes in `CHANGELOG.md` under a dated `vX.Y.Z`
heading.

## 5. Coordinate with AuraDB

Aura Connector and AuraDB ship as a coordinated pair over the Aura Wire Protocol.
Release the connector before the AuraDB version that depends on it, so AuraDB's
connector-conformance CI can install the published client. Confirm the AWP and
Query IR compatibility notes in the `CHANGELOG.md` entry.

## 6. Tag and push

```bash
git commit -m "Release Aura Connector vX.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

Keep release commits free of `Co-authored-by` trailers. If a local commit
template injects trailers, point it at an empty template or unset it:

```bash
git config commit.template ""
git config --unset-all trailer.co-authored-by || true
```

## 7. Publish to PyPI via a GitHub release

1. On GitHub, draft a new Release from the `vX.Y.Z` tag.
2. Paste the changelog entry as the release notes.
3. Publish the release.

Publishing the release triggers `.github/workflows/publish.yml`, which builds the
sdist and wheel, runs `twine check`, and uploads to PyPI through Trusted
Publishing.

## 8. Recover from a failed release

- PyPI does not allow re-uploading the same version. If a publish fails after the
  version was uploaded, bump to the next patch version and release again.
- If the publish failed before upload (build or check error), fix the issue on
  `main`, delete the GitHub release and the tag, then recreate them:

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
```

- You can re-run the failed `publish.yml` run from the Actions tab once the cause
  is fixed, as long as the version was not already uploaded.

## Exact command sequence

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -vv
python -m build
python -m twine check dist/*
git commit -m "Release Aura Connector vX.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

Then draft and publish the GitHub release to trigger publishing.
