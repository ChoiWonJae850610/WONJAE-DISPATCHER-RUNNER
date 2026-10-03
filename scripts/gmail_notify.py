from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wonjae_dispatcher_runner.gmail_notifications import (  # noqa: E402
    GmailClient,
    Notification,
    NotificationService,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Send one idempotent Dispatcher Gmail notice.")
    result.add_argument("--project", required=True)
    result.add_argument("--task-id", required=True)
    result.add_argument("--attempt", required=True, type=int)
    result.add_argument("--status", required=True)
    result.add_argument("--profile", default="")
    result.add_argument("--control-sha", default="")
    result.add_argument("--source-sha", default="")
    result.add_argument("--product-pr", default="")
    result.add_argument("--exact-head-sha", default="")
    result.add_argument("--integration-evidence", default="")
    result.add_argument("--smallest-next-action", default="")
    return result


def main() -> int:
    args = parser().parse_args()
    username = os.environ.get("GMAIL_USERNAME", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    service = NotificationService(GmailClient(username, app_password))
    outcome = service.deliver(
        Notification(
            project=args.project,
            task_id=args.task_id,
            attempt=args.attempt,
            status=args.status,
            profile=args.profile,
            control_sha=args.control_sha,
            source_sha=args.source_sha,
            product_pr=args.product_pr,
            exact_head_sha=args.exact_head_sha,
            integration_evidence=args.integration_evidence,
            smallest_next_action=args.smallest_next_action,
        )
    )
    cleaned = ",".join(outcome["cleaned_keys"]) or "none"
    print(
        "GITHUB_NATIVE_GMAIL_OK "
        f"key={outcome['notification_key']} sent={outcome['sent']} cleaned={cleaned}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
