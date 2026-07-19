# Release Process

HPX publishes wheel and source distributions through GitHub Actions trusted
publishing. No long-lived PyPI API token is stored in GitHub.

The workflow has two paths:

- a manual run from an exact version tag publishes to TestPyPI;
- publishing the GitHub Release for that tag publishes the same validated
  source revision to PyPI.

Both paths build fresh distributions, validate package metadata and resources,
and clean-install the wheel and sdist on Python 3.11 and 3.12 before upload.
Production publishing cannot be triggered manually.

## One-time repository setup

Create GitHub environments named `testpypi` and `pypi`. Add required reviewers
and deployment tag rules where the repository plan supports them. The
environment names are part of the trusted-publisher identity and must match the
workflow exactly.

The `helia-profiler` project does not yet exist on either package index. Create
pending trusted publishers with these values:

| Setting | TestPyPI and PyPI value |
| --- | --- |
| Owner | `AmbiqAI` |
| Repository | `helia-profiler` |
| Workflow | `publish.yml` |
| Environment | `testpypi` on TestPyPI; `pypi` on PyPI |

Trusted publisher setup is security-sensitive. A typo can grant another
workflow publishing authority. Follow the official
[PyPI trusted-publisher instructions](https://docs.pypi.org/trusted-publishers/)
and verify every value before saving.

## Prepare a release candidate

1. Update `project.version` in `pyproject.toml` and `__version__` in
   `src/helia_profiler/_version.py` to the same new PEP 440 version. PyPI
   versions are immutable and cannot be reused.
2. Run `uv lock` and commit both `pyproject.toml` and `uv.lock`.
3. Run CI and the appropriate hardware release matrix on the release commit.
4. Merge the release preparation PR to `main`.
5. Create and push an annotated `v<version>` tag on that merged commit.

The workflow rejects a tag that does not exactly match `project.version` or
whose commit is not contained in the default branch. The existing `v0.1.0` tag
predates this workflow, so the first automated release must use a new version.

## Rehearse on TestPyPI

In GitHub Actions, run **Publish Python package** manually and select the exact
version tag as the workflow ref. A branch ref is rejected. The run publishes
only to TestPyPI.

Check the rendered metadata and perform a clean installation without allowing
PyPI to satisfy the HPX package itself:

```bash
uv tool install --index-url https://test.pypi.org/simple/ \
  --index-strategy unsafe-best-match helia-profiler==<version>
hpx --version
```

TestPyPI does not mirror all dependencies. If dependency resolution from that
command fails, download the HPX wheel from TestPyPI and install it while using
PyPI for dependencies; do not weaken the production publishing workflow.

## Publish to PyPI

Create a GitHub Release from the tested tag, include release notes and hardware
validation evidence, and publish it. The release event builds and verifies the
distributions again, then enters the protected `pypi` environment. PyPI mints a
short-lived OIDC credential for that job. The publishing action also generates
and uploads digital attestations by default.

## Recovery

Published files and versions cannot be replaced. If a release is defective:

1. yank the affected version on PyPI so existing explicit pins still work;
2. document the reason in the GitHub Release;
3. fix the issue and publish a higher version through the complete process.

Never enable `skip-existing` for production or move an existing version tag to
retry a failed release. A workflow failure before upload can be rerun at the
same immutable tag after correcting external environment configuration.
