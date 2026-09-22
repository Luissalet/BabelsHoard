from pathlib import Path

from tests.faustus_manifest import check_repo

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_passes_faustus_checks():
    data = check_repo(REPO_ROOT)
    assert data["id"] == "babel"
    assert data["mcp"]["transport"] == "stdio"
