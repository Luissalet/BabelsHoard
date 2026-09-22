from pathlib import Path

from babels_hoard import environments


def test_builtin_env_registers_once(conn):
    id1 = environments.builtin_env_id(conn)
    id2 = environments.builtin_env_id(conn)
    assert id1 == id2
    rows = conn.execute("SELECT COUNT(*) c FROM environments").fetchall()
    assert rows[0]["c"] == 1


def test_probe_reports_distributions(probe):
    assert probe["python_version"]
    names = {d["name"].lower() for d in probe["distributions"]}
    assert "pytest" in names or "fastapi" in names


def test_register_environment_from_project_dir_detects_venv(conn, tmp_path):
    project = tmp_path / "myproj"
    (project / ".venv" / "bin").mkdir(parents=True)
    fake_python = project / ".venv" / "bin" / "python"
    fake_python.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
    fake_python.chmod(0o755)
    detected = environments.detect_project_python(project)
    assert detected == fake_python


def test_register_environment_missing_path_raises(conn, tmp_path):
    import pytest

    with pytest.raises(environments.ProbeError):
        environments.register_environment(conn, str(tmp_path / "does-not-exist"))


def test_resolve_env_falls_back_to_builtin(conn, builtin_env):
    resolved = environments.resolve_env(conn, None)
    assert resolved["id"] == builtin_env["id"]
