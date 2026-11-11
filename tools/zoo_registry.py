# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "certifi>=2026.10.19",
#     "urllib3>=2.8.0",
#     "helia-model-zoo @ git+https://github.com/AmbiqAI/helia-model-zoo.git@0124dea005e876b9589537180c0b1f111d115699",
#     "pyrage>=1.4.0",
#     "pyyaml>=6.0",
# ]
# ///
"""Build an ``hpx validate --models-file`` registry from pinned Zoo models.

Reads ``tools/zoo_models.yaml``, fetches each model with the
helia-model-zoo client, refuses any file whose sha256 differs from its pin,
and writes a registry that ``hpx validate --models-file`` loads:

    uv run tools/zoo_registry.py --output build/zoo/models.yaml

The script declares its own dependencies, so ``uv run`` installs the Zoo
client in an isolated environment rather than in the HPX project.

Private models live in ``tools/zoo_private.yaml.age``, an age-encrypted list
whose ``hf:`` entries name files as ``<repo>@<commit>/<path>``.
``--private-output`` writes them to a second registry under opaque IDs
(``p-<sha256 prefix>``). It needs the age key (``ZOO_AGE_KEY``,
``ZOO_AGE_KEY_FILE`` or ``~/.config/helia-profiler/zoo-private.key``) and
``HF_TOKEN`` (or ``hf auth login``); without either, private models are
skipped with a warning. Errors for private models name only the opaque ID.
Edit the list with ``tools/zoo_private.py``. Private entries may carry
``title``, ``task`` and ``domain`` for the dashboard, as Zoo records do;
the registry ignores them.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
DEFAULT_LIST = _HERE / "zoo_models.yaml"
PRIVATE_LIST = _HERE / "zoo_private.yaml.age"
RECIPIENTS = _HERE / "zoo_private.recipients"
DEFAULT_KEY = Path("~/.config/helia-profiler/zoo-private.key")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_HF_URI = re.compile(r"(?P<repo>(?:datasets/)?[\w.-]+/[\w.-]+)@(?P<rev>[0-9a-f]{40})/(?P<path>\S+)")
_HF_HOST = "https://huggingface.co"
# EngineType values; a test keeps them equal.
ENGINES = frozenset({"helia-rt", "helia-aot", "litert", "executorch"})

# Dashboard labels; hf entries only.
LABELS = ("title", "task", "domain")

Resolver = Callable[[str], Any]
# (hf uri, sha256) -> local file
Fetcher = Callable[[str, str], Path]


def parse_model_list(text: str, label: str | Path) -> list[dict[str, Any]]:
    """The validated entries of a Zoo model list."""
    document = yaml.safe_load(text)
    entries = document.get("models") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{label}: needs a non-empty 'models' list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: each model must be a mapping")
        if ("zoo" in entry) == ("hf" in entry):
            raise ValueError(f"{label}: model entry needs one of 'zoo' or 'hf'")
        keys = ("zoo", "rev") if "zoo" in entry else ("hf", "family")
        for key in (*keys, "precision", "sha256", "arena_size"):
            if key not in entry:
                raise ValueError(f"{label}: model entry lacks {key!r}")
        if "zoo" in entry and not _REVISION.fullmatch(str(entry["rev"])):
            raise ValueError(f"{label}: rev must be a 40-hex commit")
        if "hf" in entry and not _HF_URI.fullmatch(str(entry["hf"])):
            raise ValueError(f"{label}: hf must be <repo>@<40-hex commit>/<path>")
        if not _SHA256.fullmatch(str(entry["sha256"])):
            raise ValueError(f"{label}: sha256 must be 64 lowercase hex")
        skip = entry.get("skip", {})
        if not isinstance(skip, dict) or not all(
            isinstance(reason, str) and reason.strip() for reason in skip.values()
        ):
            raise ValueError(f"{label}: skip must map engine to reason")
        if unknown := sorted(set(map(str, skip)) - ENGINES):
            raise ValueError(f"{label}: skip names unknown engine {', '.join(unknown)}")
        if labels := [key for key in LABELS if key in entry]:
            if "zoo" in entry:
                raise ValueError(f"{label}: zoo entries take labels from the Zoo record")
            if not all(isinstance(entry[key], str) and entry[key].strip() for key in labels):
                raise ValueError(f"{label}: {', '.join(labels)} must be non-empty text")
        arena = entry["arena_size"]
        if isinstance(arena, bool) or not isinstance(arena, int) or arena <= 0:
            raise ValueError(f"{label}: arena_size must be a positive integer")
        model_id = _model_id(entry)
        if model_id in seen:
            raise ValueError(f"{label}: duplicate model {model_id}")
        seen.add(model_id)
    return entries


def load_model_list(path: Path) -> list[dict[str, Any]]:
    """The validated entries of a public list."""
    entries = parse_model_list(path.read_text(encoding="utf-8"), path)
    if any("hf" in entry for entry in entries):
        raise ValueError(f"{path}: hf entries belong in the private list")
    return entries


def parse_private_list(text: str) -> list[dict[str, Any]]:
    """The validated entries of the decrypted private list."""
    entries = parse_model_list(text, "private list")
    ids = [private_id(entry) for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("private list: two models share a sha256 prefix")
    return entries


def private_id(entry: dict[str, Any]) -> str:
    """Opaque ID; public logs show only this."""
    return f"p-{entry['sha256'][:8]}"


def _family(entry: dict[str, Any]) -> str:
    return entry.get("zoo") or entry["family"]


def _model_id(entry: dict[str, Any]) -> str:
    return f"{_family(entry)}-{entry['precision']}"


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_registry(
    entries: list[dict[str, Any]],
    resolve: Resolver,
    fetch: Fetcher | None = None,
    opaque: bool = False,
) -> dict[str, Any]:
    """The ``--models-file`` document for these entries.

    ``fetch`` None skips ``hf:`` entries. ``opaque`` keys models by
    :func:`private_id` and keeps names out of errors.
    """
    models: dict[str, Any] = {}
    for entry in entries:
        if "hf" in entry and fetch is None:
            continue
        model_id = private_id(entry) if opaque else _model_id(entry)
        if "hf" in entry:
            source = f"hf://{entry['hf']}"
        else:
            source = f"zoo://{entry['zoo']}/{entry['precision']}@{entry['rev']}"
        label = model_id if opaque else source
        try:
            if "hf" in entry:
                assert fetch is not None
                model_path = Path(fetch(entry["hf"], entry["sha256"])).resolve()
                title = entry["family"]
            else:
                resolved = resolve(source)
                model_path = Path(resolved.model).resolve()
                title = resolved.record.title
        except Exception as error:
            reason = type(error).__name__ if opaque else str(error)
            raise RuntimeError(f"{label}: fetch failed: {reason}") from None
        actual = _sha256(model_path)
        if actual != entry["sha256"]:
            raise ValueError(f"{label}: sha256 {actual} differs from pin")
        models[model_id] = {
            "path": str(model_path),
            "arena_size": entry["arena_size"],
            "name": f"{title} ({entry['precision']})",
            "family": _family(entry),
            "precision": entry["precision"],
            "source": source,
        }
        if "skip" in entry:
            models[model_id]["skip"] = entry["skip"]
    return {"models": models}


def _zoo_resolver(cache: Path | None) -> Resolver:
    options = {} if cache is None else {"cache": cache}

    def resolve(source: str) -> Any:
        import helia_model_zoo

        return helia_model_zoo.resolve(source, **options)

    return resolve


def age_identity() -> str:
    """ZOO_AGE_KEY, else the key file."""
    if key := os.environ.get("ZOO_AGE_KEY", "").strip():
        return key
    path = Path(os.environ.get("ZOO_AGE_KEY_FILE") or DEFAULT_KEY).expanduser()
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def decrypt(data: bytes, identity: str) -> bytes:
    """Plaintext of age ciphertext."""
    import pyrage

    keys = [
        pyrage.x25519.Identity.from_str(line.strip())
        for line in identity.splitlines()
        if line.strip().startswith("AGE-SECRET-KEY-")
    ]
    if not keys:
        raise ValueError("age key holds no AGE-SECRET-KEY line")
    try:
        return pyrage.decrypt(data, keys)
    except pyrage.DecryptError as error:
        raise ValueError(f"age: {error}") from None


def decrypt_list(data: bytes, identity: str) -> str:
    """Plaintext of an age-encrypted list."""
    return decrypt(data, identity).decode("utf-8")


def encrypt(data: bytes, recipients: Path = RECIPIENTS, armored: bool = False) -> bytes:
    """Age ciphertext for every recipient."""
    import pyrage

    keys = [
        pyrage.x25519.Recipient.from_str(line.strip())
        for line in recipients.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("age1")
    ]
    if not keys:
        raise ValueError(f"{recipients}: no age1 recipients")
    return pyrage.encrypt(data, keys, armored=armored)


def encrypt_list(text: str, recipients: Path = RECIPIENTS) -> bytes:
    """Armored age ciphertext of a list."""
    return encrypt(text.encode("utf-8"), recipients, armored=True)


def _hf_token_path() -> Path:
    """Where ``hf auth login`` saves, per its env overrides."""
    if path := os.environ.get("HF_TOKEN_PATH"):
        return Path(path).expanduser()
    if home := os.environ.get("HF_HOME"):
        return Path(home).expanduser() / "token"
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache).expanduser() / "huggingface" / "token"


def _hf_token() -> str:
    """HF_TOKEN, else the huggingface CLI's saved token."""
    token = os.environ.get("HF_TOKEN", "").strip()
    saved = _hf_token_path()
    if not token and saved.is_file():
        token = saved.read_text(encoding="utf-8").strip()
    return token


def _hf_fetcher(token: str, cache: Path | None) -> Fetcher:
    import certifi
    import urllib3

    # Bounded: a stall holds the board lock.
    http = urllib3.PoolManager(
        ca_certs=certifi.where(),
        timeout=urllib3.Timeout(connect=30, read=120),
        retries=urllib3.Retry(total=3, backoff_factor=2),
    )
    root = (cache or Path.home() / ".cache" / "helia-model-zoo") / "hf"

    def fetch(uri: str, sha256: str) -> Path:
        match = _HF_URI.fullmatch(uri)
        assert match is not None  # parse_model_list checked it
        # Named by sha: paths reveal nothing.
        target = root / sha256 / "model.tflite"
        if target.is_file() and _sha256(target) == sha256:
            return target
        url = f"{_HF_HOST}/{match['repo']}/resolve/{match['rev']}/{match['path']}"
        response = http.request(
            "GET", url, headers={"Authorization": f"Bearer {token}"}, preload_content=False
        )
        try:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Unique name: board jobs share the cache.
            with tempfile.NamedTemporaryFile(
                dir=target.parent, suffix=".part", delete=False
            ) as stream:
                partial = Path(stream.name)
                for chunk in response.stream(1 << 20):
                    stream.write(chunk)
            partial.replace(target)
        finally:
            response.release_conn()
        return target

    return fetch


def _read_private(path: Path) -> str | None:
    """Decrypted private list; None without a key."""
    identity = age_identity()
    if not identity:
        return None
    return decrypt_list(path.read_bytes(), identity)


def _write(registry: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")


def main(
    argv: list[str] | None = None,
    resolve: Resolver | None = None,
    fetch: Fetcher | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", type=Path, default=DEFAULT_LIST, help="Zoo model list")
    parser.add_argument("--output", type=Path, required=True, help="registry YAML to write")
    parser.add_argument("--cache", type=Path, help="Zoo download cache directory")
    parser.add_argument(
        "--private", type=Path, default=PRIVATE_LIST, help="age-encrypted private list"
    )
    parser.add_argument("--private-output", type=Path, help="private registry YAML to write")
    args = parser.parse_args(argv)
    resolve = resolve or _zoo_resolver(args.cache)
    try:
        registry = build_registry(load_model_list(args.models), resolve)
        private = None
        if args.private_output:
            text = _read_private(args.private)
            token = _hf_token()
            if text is None:
                print("zoo_registry: ZOO_AGE_KEY unset; skipping private models", file=sys.stderr)
            elif not token and fetch is None:
                print("zoo_registry: HF_TOKEN unset; skipping private models", file=sys.stderr)
            else:
                fetch = fetch or _hf_fetcher(token, args.cache)
                entries = parse_private_list(text)
                private = build_registry(entries, resolve, fetch, opaque=True)
    except (OSError, RuntimeError, ValueError, KeyError, yaml.YAMLError) as error:
        print(f"zoo_registry: {error}", file=sys.stderr)
        return 1
    _write(registry, args.output)
    print(f"Wrote {len(registry['models'])} models to {args.output}")
    if private is not None and args.private_output:
        _write(private, args.private_output)
        print(f"Wrote {len(private['models'])} private models to {args.private_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
