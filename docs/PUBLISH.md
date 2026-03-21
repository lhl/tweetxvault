# Publishing Checklist

This is the release punch list for cutting a new `tweetxvault` version.

Scope:

- Use this checklist whenever preparing a new Git tag or publishing to PyPI.
- `CHANGELOG.md` is release-oriented, not an in-progress ledger. Add a new
  topmost version section when cutting a release; do not keep an `Unreleased`
  section.
- Historical tags `v0.1.0`, `v0.1.1`, and `v0.2.0` are already backfilled in
  `CHANGELOG.md`.

## Versioning

Use semver-style version bumps:

- Patch (`0.2.1`): bug fixes, performance fixes, packaging/docs-only releases,
  and low-risk UX cleanups.
- Minor (`0.3.0`): new user-facing commands, flags, archive capabilities, or
  substantial search/storage features.
- Major (`1.0.0`): intentionally breaking CLI/storage/workflow changes that need
  explicit upgrade guidance.

## Release Punch List

- [ ] Start from a clean tree: `git status -sb`
- [ ] Sync with the remote release base before cutting the version:
      `git fetch --tags origin` and `git pull --ff-only`
- [ ] Pick the next version number and decide whether the release is
      patch/minor/major
- [ ] Update version metadata in:
      `pyproject.toml`
- [ ] Update version metadata in:
      `tweetxvault/__init__.py`
- [ ] Update `CHANGELOG.md`:
      add a new topmost `## [X.Y.Z] - YYYY-MM-DD` section, keep the validation
      bullets, and update the version links at the bottom
- [ ] Update README/docs/help text if install steps, CLI behavior, or supported
      workflows changed
- [ ] Update `docs/README.md` if documentation files were added or removed
- [ ] Add a concise release entry to `WORKLOG.md` with the exact validation and
      publish commands run
- [ ] Run release validation:
      `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- [ ] Run release validation:
      `uv run ruff check`
- [ ] Run release validation:
      `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- [ ] Build fresh artifacts:
      `uv build`
- [ ] Verify package metadata/rendering:
      `uvx --from twine twine check dist/*`
- [ ] Smoke-test the built wheel before upload:
      `uv run --isolated --with dist/tweetxvault-X.Y.Z-py3-none-any.whl tweetxvault --help`
- [ ] Stage only release files explicitly and review them:
      `git add ...`, `git diff --staged --name-only`, `git diff --staged`
- [ ] Commit release metadata:
      `git commit -m "chore: prepare vX.Y.Z release metadata"`
- [ ] Create an annotated tag:
      `git tag -a vX.Y.Z -m "vX.Y.Z"`
- [ ] Push the release commit and tag:
      `git push origin main`, `git push origin vX.Y.Z`
- [ ] Publish to PyPI with the configured credentials:
      `uv publish dist/tweetxvault-X.Y.Z-py3-none-any.whl dist/tweetxvault-X.Y.Z.tar.gz`
- [ ] If `uv publish` is not configured, use the fallback upload path:
      `uvx --from twine twine upload dist/tweetxvault-X.Y.Z-py3-none-any.whl dist/tweetxvault-X.Y.Z.tar.gz`
- [ ] Verify the published install path from PyPI:
      `uvx --refresh --from "tweetxvault==X.Y.Z" tweetxvault --help`
- [ ] Verify the GitHub tag/release page and PyPI project page both show the new
      version correctly
- [ ] Confirm the tree is clean again with `git status -sb`

## Notes

- Do not publish from a dirty tree.
- Do not reuse an older `dist/` blindly; rebuild artifacts for each release.
- `uv publish` defaults to `dist/*`; either clear old artifacts first or pass the
  exact wheel + sdist paths for the version you are cutting.
- Immediate post-publish install checks may need `uvx --refresh` (or a short
  retry window) because resolver/index caches can lag behind the successful
  upload for a minute or two.
- If a historical tag is missing from `CHANGELOG.md`, backfill that entry before
  publishing the next version.
- Do not add speculative or partial release notes ahead of an actual cut; write
  the release entry when the version is being prepared.
- If a release includes breaking behavior or archive upgrade steps, add the
  upgrade note to both `README.md` and `CHANGELOG.md`.
