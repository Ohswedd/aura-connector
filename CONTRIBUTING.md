# Contributing

Thanks for your interest in improving Aura Connector. This guide covers local setup, the
test and build commands, and the expectations for pull requests.

## Setup

Requires Python 3.11 or newer. Optional native acceleration also requires a stable Rust
toolchain.

```bash
git clone https://github.com/Ohswedd/aura-connector
cd aura-connector
python -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Running the checks

The same gate that CI runs:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -vv
python -m compileall src tests examples
python -m build
```

The test suite is deterministic and needs no external services.

## Native build

The optional extension lives in `crates/aura_native`. Build it into your environment with
maturin and a Rust toolchain:

```bash
python -m pip install -e ".[dev,native]"
maturin develop --manifest-path crates/aura_native/Cargo.toml --release
aura doctor                    # native_acceleration: available
python -m pytest tests/unit/test_native_adapter.py tests/native/test_native_parity.py -vv
```

The connector must remain fully functional in pure Python. Any change to a native-backed
path must keep byte-for-byte parity with the pure-Python reference, which the native tests
enforce.

## Code style

- Formatting and linting are handled by Ruff. Run `ruff format .` before committing.
- Type checking uses mypy in strict mode against `src`.
- Public APIs are typed and avoid `Any` unless justified.
- Keep comments focused on non-obvious production logic, protocol layout, safety
  constraints, or native fallback behaviour.

## Pull requests

- Keep each pull request focused on one coherent change.
- Add or update tests for any behaviour change.
- Update the relevant docs and `CHANGELOG.md` under the `Unreleased` heading.
- Make sure the full check gate above passes locally.

## Commit trailers

Do not add `Co-authored-by` trailers unless explicitly requested by the maintainer. Keep
commit messages clean and descriptive.

## For maintainers

The release process — version bump, AuraDB coordination, tagging, and PyPI publishing — is
documented in [docs/RELEASE.md](docs/RELEASE.md).
