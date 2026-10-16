"""Review exact registered workflow maintenance offline; no writes or dispatch.

Only future authenticated SANJINWORKS Owner actions may start an executable
product maintenance workflow. This script is a read-only staging primitive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from wonjae_dispatcher_runner.lean_ci_maintenance import (
    LeanCiMaintenanceError,
    review_candidate,
    select_minimal_checks,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-checkout", required=True, type=Path)
    parser.add_argument("--product-checkout", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--control-sha", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--previous-sha256", required=True)
    parser.add_argument("--proposed-sha256", required=True)
    parser.add_argument("--candidate-file", required=True, type=Path)
    parser.add_argument(
        "--changed-file", action="append", required=True,
        help="Exact affected Git path; repeat for each, no globbing",
    )
    args = parser.parse_args()

    candidate_file = args.candidate_file
    if candidate_file.is_symlink() or not candidate_file.is_file():
        parser.error("candidate must be a regular staged review file")
    if candidate_file.resolve().is_relative_to(args.product_checkout.resolve()):
        parser.error("candidate must remain outside the clean product checkout")
    if candidate_file.resolve().is_relative_to(args.control_checkout.resolve()):
        parser.error("candidate must remain outside the control checkout")

    try:
        candidate = candidate_file.read_text(encoding="utf-8")
        selection = select_minimal_checks(tuple(args.changed_file))
        preview = review_candidate(
            control_checkout=args.control_checkout,
            product_checkout=args.product_checkout,
            project=args.project,
            expected_control_sha=args.control_sha,
            expected_product_sha=args.source_sha,
            expected_previous_sha256=args.previous_sha256,
            expected_proposed_sha256=args.proposed_sha256,
            candidate=candidate,
        )
    except (LeanCiMaintenanceError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    # Computed output is evidence of a LOCAL REVIEW, never Owner admission or CI PASS.
    output = {
        "review": asdict(preview),
        "minimal_selection": asdict(selection),
        "reviewed_candidate_bytes": len(candidate.encode("utf-8")),
        "approval_or_dispatch_performed": False,
        "product_source_modified": False,
        "pr_or_ci_executed": False,
        "state": "PREPARED_NOT_EXECUTABLE",
    }
    assert hashlib.sha256(candidate.encode("utf-8")).hexdigest() == preview.proposed_sha256
    print(json.dumps(output, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
