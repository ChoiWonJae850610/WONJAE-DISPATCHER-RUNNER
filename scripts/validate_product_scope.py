"""Final cumulative branch scope gate, including required task paths."""

import os
from pathlib import Path

from wonjae_dispatcher_runner.product_patch import load_work_order, validate_branch_scope

if __name__ == "__main__":
    validate_branch_scope(
        Path(os.environ["PRODUCT_CHECKOUT"]),
        load_work_order(Path(os.environ["WORK_ORDER_FILE"])),
    )
