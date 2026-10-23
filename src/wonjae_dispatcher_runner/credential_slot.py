"""Select a verified Direct Worker credential slot without exposing auth material.

This opt-in two-slot path is dormant while both runner-name variables are empty.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path


class CredentialSlotError(ValueError):
    """A fixed-code, non-secret credential-slot admission failure."""


def resolve_credential_slot(
    actual_runner: str, primary_runner: str, secondary_runner: str,
) -> str:
    """Keep the existing single-runner route until BOTH names are configured."""
    if not primary_runner and not secondary_runner:
        return "legacy"
    if not primary_runner or not secondary_runner:
        raise CredentialSlotError("CODEX_SLOT_RUNNER_NAMES_INCOMPLETE")
    if primary_runner == secondary_runner:
        raise CredentialSlotError("CODEX_SLOT_RUNNER_NAMES_DUPLICATE")
    if actual_runner == primary_runner:
        return "primary"
    if actual_runner == secondary_runner:
        return "secondary"
    raise CredentialSlotError("CODEX_SLOT_RUNNER_NOT_REGISTERED")


def require_independent_auth(primary_json: str, secondary_json: str) -> None:
    """Fail closed if two slots would share one refresh session.

    Examine credential material only in the trusted job's ephemeral process.
    Never log, output, hash, store or include token values in an exception.
    """
    def refresh_token(payload: str) -> str:
        try:
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise CredentialSlotError("CODEX_SLOT_AUTH_INVALID") from exc
        if not isinstance(document, dict):
            raise CredentialSlotError("CODEX_SLOT_AUTH_INVALID")
        tokens = document.get("tokens")
        if not isinstance(tokens, dict):
            raise CredentialSlotError("CODEX_SLOT_AUTH_INVALID")
        value = tokens.get("refresh_token")
        if not isinstance(value, str) or not value:
            raise CredentialSlotError("CODEX_SLOT_AUTH_INVALID")
        return value

    a = refresh_token(primary_json)
    b = refresh_token(secondary_json)
    if hmac.compare_digest(a, b):
        raise CredentialSlotError("CODEX_SLOT_AUTH_SHARED_SESSION")


def main() -> int:
    parser = argparse.ArgumentParser(description="Trusted Direct Worker slot guard")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--github-output", type=Path)
    modes.add_argument("--check-auth", action="store_true")
    args = parser.parse_args()

    try:
        if args.check_auth:
            if os.environ.get("DW_CREDENTIAL_SLOT") not in ("primary", "secondary"):
                raise CredentialSlotError("CODEX_SLOT_AUTH_MODE_INVALID")
            require_independent_auth(
                os.environ.get("CODEX_AUTH_JSON_PRIMARY", ""),
                os.environ.get("CODEX_AUTH_JSON_SECONDARY", ""),
            )
            print("CODEX_SLOT_AUTH_ISOLATED")
        else:
            slot = resolve_credential_slot(
                os.environ.get("DW_ACTUAL_RUNNER_NAME", ""),
                os.environ.get("DW_PRIMARY_RUNNER_NAME", ""),
                os.environ.get("DW_SECONDARY_RUNNER_NAME", ""),
            )
            if not args.github_output.is_file() or args.github_output.is_symlink():
                raise CredentialSlotError("CODEX_SLOT_OUTPUT_UNAVAILABLE")
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write("slot=" + slot + "\n")
            print("CODEX_CREDENTIAL_SLOT=" + slot)
    except CredentialSlotError as exc:
        parser.exit(2, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
