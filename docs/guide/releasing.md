# Release Process

HPX uses Release Please for versioning, release notes, tags, and GitHub
Releases. `uv` builds, verifies, and publishes the wheel and source
distribution through PyPI trusted publishing. No long-lived package-index
credential is stored in GitHub.

Pushes to `main` update a Release Please pull request. Merging that pull request
creates the version tag and GitHub Release, validates package metadata and
resources, clean-installs the wheel and sdist on Python 3.11 and 3.12, and then
publishes to PyPI.

## One-time repository setup

Create a GitHub environment named `pypi`. Add required reviewers and
deployment-branch rules where the repository plan supports them.

Until the first successful upload creates the project, configure the pending
PyPI trusted publisher with these values:

| Setting | PyPI value |
| --- | --- |
| Owner | `AmbiqAI` |
| Repository | `helia-profiler` |
| Workflow | `publish.yml` |
| Environment | `pypi` recommended; an unset environment accepts any environment |

Trusted publisher setup is security-sensitive. Follow the official
[PyPI trusted-publisher instructions](https://docs.pypi.org/trusted-publishers/)
and verify every value before saving.

## Prepare and verify a release

Use Conventional Commits on `main`. Release Please maintains a release pull
request that updates:

- `CHANGELOG.md`;
- `project.version` in `pyproject.toml`;
- `__version__` in `src/helia_profiler/_version.py`;
- the editable HPX package version in `uv.lock`.

The workflow explicitly dispatches CI for that automation-created pull request.
Before merging it:

1. Confirm the proposed version and generated changelog.
2. Require all normal CI and package checks to pass.
3. Run the appropriate hardware release matrix against the release branch and
   archive its validation bundle.

The existing `v0.1.0` tag predates this workflow. Release Please starts from
the tracked `0.1.0` manifest and will create a new immutable version and tag.

## Publish to PyPI

Merge the verified Release Please pull request. Release Please creates the tag
and GitHub Release from `main`. The workflow checks that the tag, project
version, and source version agree, rebuilds and verifies both distributions,
then runs `uv publish --trusted-publishing always` in the protected `pypi`
environment.

`uv publish` uses GitHub's short-lived OIDC credential. It retries uploads and
will accept an identical file that was already uploaded to PyPI, while refusing
to replace different content for an existing filename. `uv` supports uploading
PEP 740 attestations but does not currently generate them itself.

## Recovery

Published files and versions cannot be replaced. If a release is defective:

1. yank the affected version on PyPI so existing explicit pins still work;
2. document the reason in the GitHub Release;
3. fix the issue and publish a higher version through the complete process.

Never move an existing version tag. A workflow interrupted during upload can
be rerun for the same immutable release; `uv` verifies any already-present file
before skipping it.
