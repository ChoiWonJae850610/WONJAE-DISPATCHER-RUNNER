"""Bounded, pre-write source repair checks; exact Actions validation remains the oracle."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_FAILURE_CHARS = 80_000
MAX_SQL_FILES = 512
MAX_CHECKSUM_BYTES = 8_000_000
EVIDENCE_PREFIX = "DISPATCHER_REPAIR_EVIDENCE="
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TIMESTAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\S+\s+")
_MISSING_HEADER = re.compile(
    r"^(?:Error:\s*)?(?:[\w .-]+ )?missing (?:mandatory )?"
    r"(?:source )?(?:artifacts|files|paths):\s*", re.IGNORECASE
)
_MISSING_SINGLE = re.compile(
    r"^(?:Error:\s*)?(?:missing(?: (?:file|path|artifact))?\s*[: ]|"
    r"required (?:source )?(?:file|path|artifact) (?:is )?missing\s*:)\s*",
    re.IGNORECASE,
)


class RepairCompletenessError(RuntimeError):
    """A plan has not resolved a repository-verifiable repair obligation."""


@dataclass(frozen=True)
class RepairEvidence:
    missing_paths: tuple[str, ...]
    checksum_manifests: tuple[str, ...]
    unscoped_missing_paths: bool = False


def _safe_path(path: str) -> bool:
    value = Path(path)
    return bool(path) and not value.is_absolute() and ".." not in value.parts


def _read(repo: Path, path: str, proposed: dict[str, str | None]) -> bytes | None:
    if not _safe_path(path):
        raise RepairCompletenessError("repair evidence contains an unsafe path")
    target = repo / path
    if not target.resolve().is_relative_to(repo.resolve()):
        raise RepairCompletenessError("repair evidence path escapes the repository")
    if path in proposed:
        value = proposed[path]
        return None if value is None else value.encode("utf-8")
    if not target.is_file():
        return None
    if target.stat().st_size > MAX_CHECKSUM_BYTES:
        raise RepairCompletenessError("repair evidence file exceeds the bounded read limit")
    return target.read_bytes()


def collect_repair_evidence(failure: str, allowed_paths: tuple[str, ...]) -> RepairEvidence:
    """Only recognize explicit allowlisted paths in missing diagnostics or a JSON marker.

    Arbitrary path mentions, stack frames and prose are not mandatory-touch evidence.
    The JSON marker supports missing_paths and checksum_manifests, never a model's
    assertion that a validator is defective.
    """
    allowed = set(allowed_paths)
    missing: set[str] = set()
    manifests: set[str] = set()
    unscoped_missing_paths = False
    in_missing_list = False
    for raw in failure[-MAX_FAILURE_CHARS:].splitlines():
        line = _TIMESTAMP.sub("", _ANSI.sub("", raw)).strip()
        if line.startswith(EVIDENCE_PREFIX):
            try:
                evidence = json.loads(line[len(EVIDENCE_PREFIX):])
            except (ValueError, TypeError) as exc:
                raise RepairCompletenessError("invalid structured repair evidence") from exc
            if not isinstance(evidence, dict) or set(evidence) - {
                "missing_paths", "checksum_manifests"
            }:
                raise RepairCompletenessError("unsupported structured repair evidence")
            for key, destination in (("missing_paths", missing), ("checksum_manifests", manifests)):
                values = evidence.get(key, [])
                if not isinstance(values, list) or len(values) > MAX_SQL_FILES or any(
                    not isinstance(value, str) or not _safe_path(value) for value in values
                ):
                    raise RepairCompletenessError("invalid structured repair evidence paths")
                destination.update(value for value in values if value in allowed)
                if any(value not in allowed for value in values):
                    unscoped_missing_paths = True
            in_missing_list = False
            continue
        header = _MISSING_HEADER.match(line)
        single = _MISSING_SINGLE.match(line)
        if header or single:
            remainder = line[(header or single).end():]
            # Inline comma-separated exact paths; do not tokenize unrestricted prose.
            missing.update(
                value.strip(" `'") for value in remainder.split(",")
                if value.strip(" `'") in allowed
            )
            if any(
                _safe_path(value.strip(" `'")) and re.fullmatch(r"[\w./-]+", value.strip(" `'"))
                and value.strip(" `'") not in allowed
                for value in remainder.split(",") if value.strip()
            ):
                unscoped_missing_paths = True
            in_missing_list = bool(header)
        elif in_missing_list:
            value = line.removeprefix("- ").strip(" `'")
            if value in allowed:
                missing.add(value)
            elif not _safe_path(value) or not re.fullmatch(r"[\w./-]+", value):
                in_missing_list = False
            else:
                unscoped_missing_paths = True
        if (
            "Migration checksum manifest must contain exactly the canonical migration set." in line
            or line.startswith((
                "Error: Migration checksum mismatch:", "Migration checksum mismatch:"
            ))
            or re.fullmatch(
                r"(?:Error: )?[Mm]igration [\w./-]+ checksum missing(?: from manifest)?\.?", line
            )
        ):
            manifests.update(path for path in allowed if Path(path).name == "SHA256SUMS")
    return RepairEvidence(tuple(sorted(missing)), tuple(sorted(manifests)), unscoped_missing_paths)


def _checksum_errors(repo: Path, manifest: str, proposed: dict[str, str | None]) -> list[str]:
    directory = Path(manifest).parent
    target = repo / directory
    if not target.resolve().is_relative_to(repo.resolve()):
        raise RepairCompletenessError("checksum directory escapes the repository")
    names = {path.name for path in target.glob("*.sql") if path.is_file()}
    names.update(Path(path).name for path in proposed if Path(path).parent == directory
                 and Path(path).suffix == ".sql" and proposed[path] is not None)
    if len(names) > MAX_SQL_FILES:
        raise RepairCompletenessError("checksum directory exceeds the bounded file limit")
    expected: dict[str, str] = {}
    total = 0
    for name in sorted(names):
        content = _read(repo, (directory / name).as_posix(), proposed)
        if not content:
            return [f"nonempty migration required: {(directory / name).as_posix()}"]
        total += len(content)
        if total > MAX_CHECKSUM_BYTES:
            raise RepairCompletenessError("checksum inputs exceed the bounded read limit")
        expected[name] = hashlib.sha256(content).hexdigest()
    content = _read(repo, manifest, proposed)
    try:
        lines = content.decode("utf-8").splitlines() if content is not None else []
    except UnicodeDecodeError:
        lines = []
    actual: dict[str, str] = {}
    malformed = False
    for line in lines:
        match = re.fullmatch(r"([a-f0-9]{64})  ([\w.-]+\.sql)", line)
        if match is None or match[2] in actual:
            malformed = True
            continue
        actual[match[2]] = match[1]
    if content is not None and not malformed and actual == expected:
        return []
    required = "\n".join(f"{digest}  {name}" for name, digest in expected.items())
    return [f"{manifest} must match the complete SQL set and exact bytes. "
            f"Expected content:\n{required}"]


def check_repair_completeness(
    repo: Path,
    allowed_paths: tuple[str, ...],
    evidence: RepairEvidence,
    proposed: dict[str, str | None],
    changed: tuple[str, ...],
) -> None:
    """Check the virtual final tree before any trusted write or repair commit."""
    manifests = set(evidence.checksum_manifests)
    for path in changed:
        value = Path(path)
        if value.name == "SHA256SUMS":
            manifests.add(path)
        elif value.suffix == ".sql":
            sibling = (value.parent / "SHA256SUMS").as_posix()
            if sibling in allowed_paths or (repo / sibling).is_file():
                manifests.add(sibling)
    # Existing files in a missing list need no artificial touch. Still ensure the
    # proposed final tree retains every reported artifact.
    problems = [f"missing artifact: {path}" for path in evidence.missing_paths
                if not _read(repo, path, proposed)]
    for manifest in sorted(manifests):
        problems.extend(_checksum_errors(repo, manifest, proposed))
    if problems:
        raise RepairCompletenessError("Incomplete repair; resolve ALL obligations:\n"
                                      + "\n".join(problems))

    validators = [
        path for path in changed
        if Path(path).suffix in {".py", ".js", ".mjs", ".ts", ".sh"}
        and re.match(r"(?:validate|validator|check)[-_.]", Path(path).name)
    ]
    if validators:
        # Permit checker edits only with a state contradiction: its explicit missing
        # or checksum diagnostic is already false on the unchanged checkout.
        has_source_evidence = bool(evidence.missing_paths or evidence.checksum_manifests)
        baseline_missing = any(not _read(repo, path, {}) for path in evidence.missing_paths)
        baseline_checksum = any(_checksum_errors(repo, path, {})
                                for path in evidence.checksum_manifests)
        if (not has_source_evidence or baseline_missing or baseline_checksum
                or evidence.unscoped_missing_paths):
            raise RepairCompletenessError(
                "Validator edit lacks repository-verifiable defect evidence. Fix source/test/doc/"
                "manifest obligations; changing validator diagnostics is not a complete repair."
            )
