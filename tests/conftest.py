import os
import shutil
from pathlib import Path

import pytest

os.environ["RELATE_SNAPSHOT_FORMAT"] = "json"

from spillikin import workflow  # noqa: E402
from spillikin.store import Store  # noqa: E402

MERIDIAN = Path(__file__).resolve().parents[1] / "examples" / "meridian"


@pytest.fixture
def meridian(tmp_path: Path) -> tuple[Path, Store]:
    """A private copy of the example project with a freshly built store."""
    root = tmp_path / "meridian"
    shutil.copytree(MERIDIAN, root, ignore=shutil.ignore_patterns("store"))
    store = Store(root / "store")
    workflow.build(root, store)
    return root, store
