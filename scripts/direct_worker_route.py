from __future__ import annotations

import argparse
from pathlib import Path

from wonjae_dispatcher_runner.direct_worker import load_direct_worker_route


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    route = load_direct_worker_route(Path(args.registry), args.project)
    values = {
        "repository": route.repository,
        "branch": route.branch,
        "validation_workflow_file": route.validation_workflow_path.rsplit("/", 1)[-1],
        "validation_workflow_name": route.validation_workflow_name,
    }
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={value}\n")
    else:
        for key, value in values.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
