# Certificate trust remediation

[Issue #587](https://github.com/AmbiqAI/helia-profiler/issues/587) records HPX's remediation of the
[emSign certificate incident](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576/1).
The upstream minimums are certifi 2026.10.19, uv 0.13.4 and pip 26.2.2.
Updating one does not update the others: uv and pip carry their own trust stores.
Do not disable TLS verification to bootstrap an update.

Consumer remediation shipped in 0.4.1, and
[#587 was closed at owner direction](https://github.com/AmbiqAI/helia-profiler/issues/587#issuecomment-6403728983).
Existing installations do not upgrade automatically. Older local environments,
external images, OS trust stores and custom CA stores remain the responsibility
of their environment or infrastructure owners; they are not unfinished HPX
consumer remediation. The checks below support those owners.

## Repository controls

- The public package requires patched certifi, including wheel installations
  outside this repository. The resolver constraint and lock also protect project
  installs and optional extras. The standalone zoo registry script declares its
  own certifi floor because its PEP 723 environment resolves separately.
- CI pins uv 0.13.5, whose managed Python downloads include patched pip.
  Jobs using setup-python upgrade installed pip through patched uv before use.
  The self-hosted validation workflow reinstalls its managed Python rather than
  reusing an old interpreter bundle.
- Pre-commit uses virtualenv 21.14.13, which bundles patched pip for HPX's supported
  Python versions. The workflow cache key invalidates old hook environments.
- Nix keeps the firmware compiler input unchanged. A separate pinned input
  supplies uv and the CA bundle; the Python override replaces its ensurepip
  wheel with the upstream checksum-verified patched pip wheel. The development
  shell selects that CA bundle. `checks.<system>.certificate-tools` verifies the
  executable uv, Python ensurepip and the contributor virtualenv seed. The Nix
  contributor pre-commit uses that patched virtualenv; evaluation alone does not
  execute these checks.

## Developer and runner rollout

Start with uv 0.13.5 or later obtained through a trusted distribution channel.
If your existing installer may carry the affected store, follow the upstream
bootstrap instructions and verify its published checksum. Do not use an old
pip to upgrade itself over the affected TLS path.

For an existing HPX checkout, rebuild its environment with a newly downloaded
managed Python. Preserve any unrelated environments before replacing them:

```sh
uv --version
uv python install --reinstall 3.12
uv sync --locked --all-groups --extra aot --extra analysis --python 3.12
uv run python -c 'import certifi, ensurepip; print(certifi.__version__, certifi.where()); print("ensurepip", ensurepip.version())'
```

`uv sync` normally creates a pip-free virtual environment. If pip is installed
in an environment you retain, update it through patched uv and check that exact
interpreter:

```sh
uv pip install --python /path/to/environment/python 'pip>=26.2.2'
/path/to/environment/python -m pip --version
```

Upgrading installed pip does not replace an old interpreter's ensurepip wheel.
Reinstall that interpreter before creating more pip-seeded environments. For
pre-commit, reinstall the tool with its patched seeder and rebuild hook caches:

```sh
uv tool install --force --with virtualenv==21.14.13 pre-commit==4.6.0
pre-commit clean
pre-commit run --all-files
```

For Nix, enter the updated locked shell and run the executable check on your host:

```sh
nix flake check --no-build --all-systems --no-update-lock-file
nix build '.#checks.x86_64-linux.certificate-tools' --no-link
nix develop
uv --version
python -c 'import certifi, ensurepip; print(certifi.__version__); print(ensurepip.version())'
```

Use your own supported system name in the check path. Rebuild external container
images without cached affected layers, and update OS trust stores through their
owners. HPX maintains no container image in this repository. Custom enterprise
CA stores also need their owner's review; a package update cannot prove those
stores are clean.

## Environment verification evidence

When checking a retained environment, record actual commands, interpreter paths,
package/tool versions, certificate store paths and a verified HTTPS fetch in
that environment owner's maintenance record. Inspect pip's vendored store as
well as certifi's store when pip is installed. A lockfile change, successful
flake evaluation or a green test suite alone is not deployment evidence.

Use the following ownership boundaries when maintaining each surface:

| Surface | Required disposition |
| --- | --- |
| Published HPX package | Consumer remediation shipped in 0.4.1; consumers upgrade and resolve dependencies. |
| Developer and review environments | Rebuilt or inspected individually, including nested hook/tool environments. |
| GitHub-hosted CI | Fixed workflow runs successfully on the exact revision. |
| Self-hosted board runner | Owner runs the fixed bootstrap and records actual versions; no board experiment is required to check trust tooling. |
| Nix users | Updated lock and rebuilt shell/check; old store paths remain old. |
| External images, OS and custom CA stores | Owner confirms rebuild/update; not inferred from HPX CI. |

These checks do not reopen #587 or assert that every retained environment has
been inspected. A repository fix or package release does not update an existing
environment's trust stores.
