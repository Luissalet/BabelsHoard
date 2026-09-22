import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from babels_hoard import db, environments  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "babel.db")
    yield connection
    connection.close()


@pytest.fixture()
def builtin_env(conn):
    env_id = environments.builtin_env_id(conn)
    return environments.get_environment(conn, env_id)


@pytest.fixture()
def probe(builtin_env):
    return environments.probe_python(Path(builtin_env["python_path"]))
