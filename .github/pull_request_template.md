<!--
  Thanks for contributing! Fill in the sections below, then check the
  versioning note before you merge.
-->

## What

<!-- A short description of the change and why it's needed. -->

## How

<!-- Notable implementation details, trade-offs, or follow-ups. -->

## Checklist

- [ ] PR checks pass
- [ ] Tests added/updated for the change
- [ ] Version label applied if this is more than a patch (see below)

## Versioning

**You don't need to bump the version manually.** When this PR merges to `master`,
a [GitHub Action](.github/workflows/version.yml) reads the latest semver tag,
computes the next one, and tags the merge commit. The package version is derived
from that tag by setuptools_scm — nothing is hardcoded in `pyproject.toml`.

The bump level is controlled by labels on this PR:

| Label on PR          | Result            | Example         |
| -------------------- | ----------------- | --------------- |
| _(none)_             | patch bump        | `2.0.1 → 2.0.2` |
| `new minor version`  | minor bump        | `2.0.1 → 2.1.0` |
| `new major version`  | major bump        | `2.0.1 → 3.0.0` |

Apply at most one of the version labels. If both are present, `new major version`
wins.
