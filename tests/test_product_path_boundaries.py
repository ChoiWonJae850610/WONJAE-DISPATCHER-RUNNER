import hashlib
from dataclasses import replace

import pytest
from test_readonly_plan_recovery import fixture

from wonjae_dispatcher_runner import product_patch as patch


@pytest.mark.parametrize("outside", [True, False])
def test_text_binary_and_context_cannot_follow_unregistered_symlink(tmp_path, outside):
    repo, work, _, _ = fixture(tmp_path, "patch")
    target = tmp_path / "outside.txt" if outside else repo / "other.txt"
    target.write_text("protected\n")
    (repo / "link.txt").symlink_to(target)
    order = replace(patch.load_work_order(work), allowed_paths=("link.txt",),
                    required_changed_paths=("link.txt",), required_reads=("link.txt",))
    for reader in (patch._required_context, patch._repair_context):
        with pytest.raises(patch.ProductPilotError):
            reader(repo, order)
    with pytest.raises(patch.ProductPilotError):
        patch.apply_edit_plan(repo, (patch.ProductEdit("link.txt", "write", "", "changed"),), order)
    content = b"synthetic icon"
    asset = patch.ProductBinaryAsset("link.txt", content, hashlib.sha256(content).hexdigest())
    order = replace(order, binary_assets=(asset,))
    with pytest.raises(patch.ProductPilotError):
        patch.apply_binary_assets(repo, order)
    assert target.read_text() == "protected\n"


def test_binary_asset_batch_preflights_all_parent_symlinks_before_any_write(tmp_path):
    repo, work, _, _ = fixture(tmp_path, "patch")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "aliased").symlink_to(outside, target_is_directory=True)
    content = b"synthetic asset"
    assets = tuple(patch.ProductBinaryAsset(path, content, hashlib.sha256(content).hexdigest())
                   for path in ("safe.png", "aliased/icon.png"))
    order = replace(patch.load_work_order(work), allowed_paths=tuple(a.path for a in assets),
                    required_changed_paths=(), binary_assets=assets)
    with pytest.raises(patch.ProductPilotError, match="escapes"):
        patch.apply_binary_assets(repo, order)
    assert not (repo / "safe.png").exists() and not (outside / "icon.png").exists()
