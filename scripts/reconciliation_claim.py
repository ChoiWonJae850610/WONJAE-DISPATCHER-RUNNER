from __future__ import annotations

import argparse
from pathlib import Path

from wonjae_dispatcher_runner.reconciliation import load_claim


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--github-output", required=True)
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8")
    claim = load_claim(Path(args.registry), args.project, body)
    values = {
        "repository": claim.repository,
        "branch": claim.branch,
        "validation_workflow_file": claim.validation_workflow_file,
        "product_pr": str(claim.product_pr),
        "expected_head": claim.expected_head,
    }
    with Path(args.github_output).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
