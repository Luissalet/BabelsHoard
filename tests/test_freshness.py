"""Version-exactness: after an upgrade the next lookup answers from the new
version, never from the stale index; dependency names map to import names."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from babels_hoard import checker, deps, environments, indexing, search


def _python_in(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _site_packages(python: Path) -> Path:
    out = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def _install_fake(site: Path, version: str, body: str) -> None:
    for old in site.glob("wobble-*.dist-info"):
        shutil.rmtree(old)
    pkg = site / "wobble"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir()
    (pkg / "__init__.py").write_text(body, encoding="utf-8")
    info = site / f"wobble-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: wobble\nVersion: {version}\n", encoding="utf-8")
    (info / "top_level.txt").write_text("wobble\n", encoding="utf-8")
    (info / "RECORD").write_text("wobble/__init__.py,,\n", encoding="utf-8")
    # make sure the folder mtime visibly changes even on coarse filesystems
    stamp = time.time() + 2
    os.utime(site, (stamp, stamp))


@pytest.fixture()
def project(tmp_path):
    venv = tmp_path / "proj" / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    return tmp_path / "proj", _python_in(venv)


def test_upgrade_is_picked_up_and_old_index_superseded(conn, project):
    proj, python = project
    site = _site_packages(python)
    _install_fake(site, "1.0.0", "def append(x):\n    ...\n")
    env = environments.register_environment(conn, str(proj))
    env.pop("probe", None)

    v1 = checker.api_check_code(conn, "import wobble\nwobble.append(1)\n", env_row=env)
    assert v1["ok"] is True and v1["libraries"] == ["wobble@1.0.0"]

    _install_fake(site, "2.0.0", "def concat(xs, ignore_index=False):\n    ...\n")
    v2 = checker.api_check_code(conn, "import wobble\nwobble.append(1)\n", env_row=env)
    assert v2["ok"] is False, v2
    assert v2["libraries"] == ["wobble@2.0.0"]
    assert "wobble 2.0.0" in v2["findings"][0]["message"]

    statuses = {r["version"]: r["status"] for r in conn.execute("SELECT version, status FROM libraries WHERE name='wobble'")}
    assert statuses == {"1.0.0": "superseded", "2.0.0": "done"}

    looked = search.lookup_with_lazy_index(conn, "wobble.concat", env=env["id"])
    assert looked["found"] is True and looked["library"] == "wobble@2.0.0"
    gone = search.lookup_with_lazy_index(conn, "wobble.append", env=env["id"])
    assert gone["found"] is False and gone["certain"] is True
    # the superseded version never answers a search
    hits = search.search(conn, "append", env=env["id"])
    assert all(h["library"] != "wobble@1.0.0" for h in hits["results"])


def test_not_installed_package_is_silent_and_not_recorded(conn, project):
    proj, _python = project
    env = environments.register_environment(conn, str(proj))
    env.pop("probe", None)
    result = checker.api_check_code(conn, "import myproject_module\nmyproject_module.anything()\n", env_row=env)
    assert result["findings"] == [] and result["unchecked"] >= 1
    assert conn.execute("SELECT COUNT(*) c FROM libraries WHERE name LIKE '%myproject_module%'").fetchone()["c"] == 0


def test_dependency_names_map_to_import_names(conn, builtin_env, probe, tmp_path):
    (tmp_path / "requirements.txt").write_text("PyJWT>=2\npython-dotenv\nnot-installed-pkg==1.0\n", encoding="utf-8")
    names = deps.direct_dependencies(tmp_path)
    assert names == ["not-installed-pkg", "pyjwt", "python-dotenv"]
    jwt = indexing.index_python_dependency(conn, env=builtin_env, probe=probe, dist_name="pyjwt")
    assert jwt is not None and jwt["name"] == "PyJWT"
    assert conn.execute("SELECT 1 FROM entries WHERE qualname='jwt.encode'").fetchone() is not None
    dotenv = indexing.index_python_dependency(conn, env=builtin_env, probe=probe, dist_name="python-dotenv")
    assert dotenv is not None and conn.execute("SELECT 1 FROM entries WHERE qualname='dotenv.load_dotenv'").fetchone()
    assert indexing.index_python_dependency(conn, env=builtin_env, probe=probe, dist_name="not-installed-pkg") is None


def test_import_name_resolves_to_the_distribution_that_ships_it(probe):
    # griffe 2.x: the "griffe" distribution is a wrapper, "griffelib" ships the code
    dist = indexing.find_distribution(probe, "griffe")
    assert dist is not None and "griffe" in dist["top_level"]
    assert indexing.find_distribution(probe, "jwt")["name"] == "PyJWT"


def test_registering_a_non_python_file_is_refused(conn, tmp_path):
    fake = tmp_path / "run_me.sh"
    fake.write_text("echo pwned\n", encoding="utf-8")
    fake.chmod(0o755)
    with pytest.raises(environments.ProbeError, match="not a Python interpreter"):
        environments.register_environment(conn, str(fake))
