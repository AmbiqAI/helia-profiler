"""heliaPROFILER's semantic hashes: what a hash means, stated in the hash.

A semantic hash is written ``<producer>.<kind>/<version>:sha256:<hex>``,
where ``<hex>`` is 64 lowercase hex digits, for example
``hpx.build/1:sha256:`` followed by the digest. The kind names what was
hashed and the version names its basis, the exact bytes hashed. Two hashes
are comparable only when producer, kind and version all match.

The basis is the value's JSON as Python's ``json.dumps`` writes it with
``sort_keys=True``, ``separators=(",", ":")`` and ``ensure_ascii=True``:
keys sorted, no whitespace, every non-ASCII character escaped as
``\\uXXXX`` (a surrogate pair beyond U+FFFF) so the bytes are ASCII, and
each float in Python's shortest round-trip ``repr`` (``0.1``, ``1e-05``).
A non-finite float (NaN or an infinity) is refused, except in an
``observation`` basis, where a capture can report a NaN counter and it is
written as the string ``"nan"``, ``"inf"`` or ``"-inf"``.

A raw artifact digest, such as ``model_sha256``, hashes a file's bytes and
stays bare hex.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

PRODUCER = "hpx"

Kind = Literal["intent", "build", "observation", "binding", "inputs"]


class _Basis(NamedTuple):
    version: int
    #: Whether a non-finite float is written as its ``repr`` string rather than refused.
    non_finite_as_text: bool = False


#: Each kind's basis. Changing what goes into a kind's basis, or how it is
#: encoded, means bumping that kind's version.
#:
#: - ``intent``: what a frozen plan asks for (:attr:`FrozenRun.intent_hash`).
#: - ``build``: the flashed image, the resolved lock, the runtime archive and the model.
#: - ``observation``: one capture's measured facts, without host paths.
#: - ``binding``: a compiled build's plan intent, lifecycle, timing scope, state
#:   policy, power wiring, runtime manifest and observed facts.
#: - ``inputs``: each pinned input's tensor name and data sha256, in model order.
_KINDS: dict[Kind, _Basis] = {
    "intent": _Basis(1),
    "build": _Basis(1),
    "observation": _Basis(1, non_finite_as_text=True),
    "binding": _Basis(1),
    "inputs": _Basis(1),
}

KIND_VERSIONS: dict[Kind, int] = {kind: basis.version for kind, basis in _KINDS.items()}

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
    ``observation``, ``binding`` and ``inputs``; :meth:`parse` reads any
    producer's.
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


def basis(kind: Kind, value: Any) -> bytes:
    """The bytes a *kind* hash of *value* covers; see the module docstring.

    Raises:
        ValueError: *value* holds a non-finite float and *kind* refuses one.
    """
    if _KINDS[kind].non_finite_as_text:
        value = _finite(value)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except ValueError as exc:
        if "Out of range float" not in str(exc):
            raise
        raise ValueError(f"The {kind} basis cannot hold a non-finite number") from exc
    return encoded.encode("ascii")


def semantic_hash(kind: Kind, value: Any) -> str:
    """The ``hpx`` semantic hash of *value* as a *kind*, at the kind's current version.

    Raises:
        ValueError: *value* holds a non-finite float and *kind* refuses one.
    """
    hexdigest = hashlib.sha256(basis(kind, value)).hexdigest()
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
    """``value`` with non-finite floats written as their ``repr`` strings."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(item) for item in value]
    return value
