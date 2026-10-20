"""heliaPROFILER's semantic hashes: what a hash means, stated in the hash.

A semantic hash is written ``<producer>.<kind>/<version>:sha256:<hex>``,
for example ``hpx.build/1:sha256:`` and 64 lowercase hex digits. The kind
names what was hashed and the version names its basis, the exact bytes
hashed: the value's canonical JSON (sorted keys, compact separators,
non-finite floats spelled as their ``repr``), UTF-8 encoded. Two hashes are
comparable only when producer, kind and version all match.

A raw artifact digest, such as ``model_sha256``, hashes a file's bytes and
stays bare hex.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

PRODUCER = "hpx"

Kind = Literal["intent", "build", "observation", "binding"]

#: Each kind's basis version. Changing what goes into a kind's basis, or how
#: it is encoded, means bumping that kind's version.
#:
#: - ``intent``: what a frozen plan asks for (:attr:`FrozenRun.intent_hash`).
#: - ``build``: the flashed image, the resolved lock, the runtime archive and the model.
#: - ``observation``: one capture's measured facts, without host paths.
#: - ``binding``: a compiled build's plan intent, lifecycle and recorded build method.
KIND_VERSIONS: dict[Kind, int] = {"intent": 1, "build": 1, "observation": 1, "binding": 1}

_NAME = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
_FORM = re.compile(
    rf"(?P<producer>{_NAME})\.(?P<kind>{_NAME})/(?P<version>[1-9][0-9]*)"
    r":sha256:(?P<hex>[0-9a-f]{64})"
)


@dataclass(frozen=True)
class SemanticHash:
    """A hash that names its producer, its kind and its basis version.

    The text form is ``<producer>.<kind>/<version>:sha256:<hex>``: producer
    and kind are lowercase words joined by ``-``, the version a positive
    integer, and the hex 64 lowercase digits. heliaPROFILER's hashes have
    the producer ``hpx`` and the kinds ``intent``, ``build``,
    ``observation`` and ``binding``; :meth:`parse` reads any producer's.
    Compare two hashes only when their producer, kind and version match.
    """

    producer: str
    kind: str
    version: int
    hex: str

    def __post_init__(self) -> None:
        if type(self.version) is not int:
            raise ValueError(f"Semantic hash version must be an integer: {self.version!r}")
        if not all(isinstance(part, str) for part in (self.producer, self.kind, self.hex)):
            raise ValueError("Semantic hash producer, kind and hex must be strings")
        if _FORM.fullmatch(str(self)) is None:
            raise ValueError(f"Not a semantic hash: {str(self)!r}")

    @classmethod
    def parse(cls, text: str) -> SemanticHash:
        """Read *text*, refusing anything not in the semantic hash form.

        Raises:
            ValueError: *text* is not ``<producer>.<kind>/<version>:sha256:<hex>``,
                for example bare hex, uppercase hex or a missing version.
        """
        match = _FORM.fullmatch(text) if isinstance(text, str) else None
        if match is None:
            raise ValueError(
                f"Not a semantic hash: {text!r}; expected <producer>.<kind>/<version>:sha256:<hex>"
            )
        return cls(match["producer"], match["kind"], int(match["version"]), match["hex"])

    def __str__(self) -> str:
        return f"{self.producer}.{self.kind}/{self.version}:sha256:{self.hex}"


def canonical_json(value: Any) -> bytes:
    """The basis bytes of *value*: canonical JSON, UTF-8 encoded."""
    encoded = json.dumps(_finite(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return encoded.encode("utf-8")


def semantic_hash(kind: Kind, value: Any) -> str:
    """The ``hpx`` semantic hash of *value* as a *kind*, at the kind's current version."""
    hexdigest = hashlib.sha256(canonical_json(value)).hexdigest()
    return str(SemanticHash(PRODUCER, kind, KIND_VERSIONS[kind], hexdigest))


def require(text: object, kind: Kind) -> str:
    """*text* when it is an ``hpx`` *kind* hash at the kind's current version.

    Raises:
        ValueError: Naming the expected form, for example for bare hex from an
            earlier heliaPROFILER.
    """
    expected = (PRODUCER, kind, KIND_VERSIONS[kind])
    try:
        parsed = SemanticHash.parse(text) if isinstance(text, str) else None
    except ValueError:
        parsed = None
    if parsed is None or (parsed.producer, parsed.kind, parsed.version) != expected:
        form = "{}.{}/{}:sha256:<hex>".format(*expected)
        raise ValueError(f"{text!r} is not an {form} hash")
    return str(parsed)


def _finite(value: Any) -> Any:
    """``value`` with non-finite floats spelled out, so a NaN counter still hashes."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(item) for item in value]
    return value
