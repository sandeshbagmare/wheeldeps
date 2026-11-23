"""Read the dependencies of a set of wheels, offline.

Building an air-gapped bundle or auditing what a vendored tree really needs usually
means guessing from filenames. The metadata is already inside each ``.whl``: this
reads it, evaluates the environment markers, and tells you which requirements the set
does not satisfy.

    >>> from wheeldeps import normalize
    >>> normalize("Zope.Interface")
    'zope-interface'

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import platform
import re
import sys
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Requirement",
    "Wheel",
    "closure",
    "current_environment",
    "evaluate_marker",
    "external_requirements",
    "normalize",
    "parse_requirement",
    "read_wheel",
    "read_wheels",
]

__version__ = "0.1.0"

_NAME = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<specifier>[^;]*)$"
)
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<compare>===|==|~=|!=|<=|>=|<|>)
      | (?P<in>not\s+in\b|\bin\b)
      | (?P<open>\()
      | (?P<close>\))
      | (?P<string>"[^"]*"|'[^']*')
      | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
    )""",
    re.VERBOSE,
)


def normalize(name: str) -> str:
    """Normalise a project name the way PEP 503 does."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


@dataclass(frozen=True)
class Requirement:
    """One ``Requires-Dist`` line."""

    name: str
    specifier: str = ""
    extras: tuple[str, ...] = ()
    marker: str | None = None
    raw: str = ""

    @property
    def canonical(self) -> str:
        return normalize(self.name)

    def __str__(self) -> str:
        return self.raw or self.name


def parse_requirement(text: str) -> Requirement:
    """Split a requirement line into name, extras, specifier and marker."""
    body, _, marker = text.partition(";")
    match = _NAME.match(body)
    if not match:
        raise ValueError(f"not a requirement: {text!r}")
    extras_text = match.group("extras") or ""
    extras = tuple(part.strip() for part in extras_text.split(",") if part.strip())
    return Requirement(
        name=match.group("name").strip(),
        specifier=(match.group("specifier") or "").strip(),
        extras=extras,
        marker=marker.strip() or None,
        raw=text.strip(),
    )


@dataclass
class Wheel:
    """The parts of a wheel's metadata this package cares about."""

    name: str
    version: str
    requirements: list[Requirement] = field(default_factory=list)
    requires_python: str | None = None
    path: Path | None = None

    @property
    def canonical(self) -> str:
        return normalize(self.name)


def _metadata_text(archive: zipfile.ZipFile, path: Path) -> str:
    for member in archive.namelist():
        parts = member.split("/")
        if len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] == "METADATA":
            return archive.read(member).decode("utf-8", "replace")
    raise ValueError(f"{path} has no .dist-info/METADATA; is it a wheel?")


def read_wheel(path: str | Path) -> Wheel:
    """Read a wheel's metadata without extracting it."""
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        text = _metadata_text(archive, path)
    name = version = ""
    requires_python: str | None = None
    requirements: list[Requirement] = []
    for line in text.splitlines():
        if not line.strip():
            if requirements or name:
                break  # the blank line ends the header block
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()
        if field_name == "name":
            name = value
        elif field_name == "version":
            version = value
        elif field_name == "requires-python":
            requires_python = value
        elif field_name == "requires-dist":
            requirements.append(parse_requirement(value))
    if not name:
        raise ValueError(f"{path} has no Name in its metadata")
    return Wheel(
        name=name,
        version=version,
        requirements=requirements,
        requires_python=requires_python,
        path=path,
    )


def read_wheels(paths: Iterable[str | Path]) -> list[Wheel]:
    return [read_wheel(path) for path in paths]


def current_environment() -> dict[str, str]:
    """The marker variables of the running interpreter."""
    return {
        "python_version": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "python_full_version": platform.python_version(),
        "implementation_name": sys.implementation.name,
        "implementation_version": platform.python_version(),
        "platform_python_implementation": platform.python_implementation(),
        "sys_platform": sys.platform,
        "platform_system": platform.system(),
        "os_name": "nt" if sys.platform == "win32" else "posix",
        "platform_machine": platform.machine(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "extra": "",
    }


def _version_key(text: str) -> tuple:
    """A comparable key for the version strings markers use."""
    parts: list[tuple[int, int, str]] = []
    for chunk in re.split(r"[.\-_+]", text.strip()):
        if not chunk:
            continue
        number = re.match(r"\d+", chunk)
        if number:
            parts.append((0, int(number.group()), chunk[number.end() :].lower()))
        else:
            parts.append((1, 0, chunk.lower()))
    return tuple(parts)


def _compare(left: str, operator: str, right: str) -> bool:
    if operator in ("==", "==="):
        return left == right
    if operator == "!=":
        return left != right
    if operator == "~=":
        return left == right or left.startswith(right.rstrip("*"))
    left_key, right_key = _version_key(left), _version_key(right)
    if operator == "<":
        return left_key < right_key
    if operator == "<=":
        return left_key <= right_key
    if operator == ">":
        return left_key > right_key
    if operator == ">=":
        return left_key >= right_key
    raise ValueError(f"unknown operator: {operator}")


class _MarkerParser:
    """Recursive descent over the small marker grammar from PEP 508."""

    def __init__(self, text: str, environment: Mapping[str, str]) -> None:
        self.text = text
        self.environment = environment
        self.tokens = list(self._tokenize(text))
        self.position = 0

    @staticmethod
    def _tokenize(text: str) -> Iterable[tuple[str, str]]:
        index = 0
        while index < len(text):
            match = _TOKEN.match(text, index)
            if not match:
                if text[index:].strip() == "":
                    return
                raise ValueError(f"cannot parse marker near {text[index:]!r}")
            index = match.end()
            kind = match.lastgroup
            value = match.group(kind)
            yield kind or "", value

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ValueError(f"marker ended early: {self.text!r}")
        self.position += 1
        return token

    def parse(self) -> bool:
        result = self._or()
        if self._peek() is not None:
            raise ValueError(f"unexpected {self._peek()[1]!r} in marker {self.text!r}")
        return result

    def _or(self) -> bool:
        result = self._and()
        while (token := self._peek()) and token[1].lower() == "or":
            self._take()
            result = self._and() or result
        return result

    def _and(self) -> bool:
        result = self._factor()
        while (token := self._peek()) and token[1].lower() == "and":
            self._take()
            result = self._factor() and result
        return result

    def _factor(self) -> bool:
        token = self._peek()
        if token and token[1].lower() == "not":
            self._take()
            return not self._factor()
        if token and token[0] == "open":
            self._take()
            result = self._or()
            closing = self._take()
            if closing[0] != "close":
                raise ValueError(f"expected ')' in marker {self.text!r}")
            return result
        return self._comparison()

    def _comparison(self) -> bool:
        kind, variable = self._take()
        if kind != "name":
            raise ValueError(f"expected a marker variable, got {variable!r}")
        operator_kind, operator = self._take()
        operator = operator.lower()
        if operator_kind not in ("compare", "in"):
            raise ValueError(f"expected an operator after {variable!r}")
        value_kind, value = self._take()
        if value_kind != "string":
            raise ValueError(f"expected a quoted value after {operator!r}")
        value = value[1:-1]
        known = self.environment.get(variable)
        if known is None:
            return False
        # PEP 508 "in" is a substring test: python_version in "3.11 3.12".
        if operator == "in":
            return known in value
        if operator == "not in":
            return known not in value
        return _compare(known, operator, value)


def evaluate_marker(marker: str | None, environment: Mapping[str, str] | None = None) -> bool:
    """Evaluate a PEP 508 environment marker. A missing marker is always true."""
    if not marker:
        return True
    return _MarkerParser(
        marker, environment if environment is not None else current_environment()
    ).parse()


def external_requirements(
    wheels: Sequence[Wheel],
    *,
    environment: Mapping[str, str] | None = None,
) -> list[Requirement]:
    """Requirements that the given set does not provide, markers applied.

    Duplicates are collapsed by normalised name, in first-seen order, then sorted.
    """
    provided = {wheel.canonical for wheel in wheels}
    seen: set[str] = set()
    found: list[Requirement] = []
    for wheel in wheels:
        for requirement in wheel.requirements:
            if requirement.canonical in provided or requirement.canonical in seen:
                continue
            if not evaluate_marker(requirement.marker, environment):
                continue
            seen.add(requirement.canonical)
            found.append(requirement)
    return sorted(found, key=lambda requirement: requirement.canonical)


def closure(
    wheels: Sequence[Wheel],
    roots: Iterable[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Names reachable from ``roots`` inside the set, including the roots themselves."""
    by_name = {wheel.canonical: wheel for wheel in wheels}
    pending = list(by_name) if roots is None else [normalize(root) for root in roots]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        for requirement in by_name[name].requirements:
            if evaluate_marker(requirement.marker, environment):
                pending.append(requirement.canonical)
    return sorted(seen)
