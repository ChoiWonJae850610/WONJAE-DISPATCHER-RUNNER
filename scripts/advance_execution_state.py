from __future__ import annotations

import argparse
from pathlib import Path

from wonjae_dispatcher_runner.direct_worker import changed_paths, load_direct_worker_route
from wonjae_dispatcher_runner.execution_state import (
    ExecutionStateError,
    advance_product_execution_state,
    load_product_execution_state,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--product-checkout", required=True)
    parser.add_argument("--command", choices=("next", "retry", "resume"), default="next")
    args = parser.parse_args()

    route = load_direct_worker_route(Path(args.registry), args.project)
    if not route.state_path:
        print("PRODUCT_EXECUTION_STATE_ADVANCE=LEGACY_HANDOFF_NOOP")
        return 0

    try:
        product_checkout = Path(args.product_checkout).resolve()
        before = load_product_execution_state(product_checkout, route.state_path, route.project)
        if args.command in ("retry", "resume") and before.next_action.type != "SOURCE_READY":
            # Only the trusted workflow may have made this protected transition on
            # a prior successful source turn whose PR validation later failed.
            # Preserve it during the same-PR retry; never advance twice.
            if before.after_source_success is not None:
                raise ExecutionStateError("retry state has unexpected pending successor")
            print(f"PRODUCT_EXECUTION_STATE_ADVANCE=ALREADY_ADVANCED_{before.next_action.type}")
            print(f"PRODUCT_EXECUTION_STATE_PATH={route.state_path}")
            return 0
        state = advance_product_execution_state(
            product_checkout,
            route.state_path,
            route.project,
            changed_paths=changed_paths(product_checkout),
        )
    except ExecutionStateError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"PRODUCT_EXECUTION_STATE_ADVANCE={state.next_action.type}")
    print(f"PRODUCT_EXECUTION_STATE_PATH={route.state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
