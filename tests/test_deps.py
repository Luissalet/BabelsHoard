from babels_hoard import deps


def test_direct_dependencies_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "x"
dependencies = ["httpx==0.28.1", "pydantic>=2,<3"]

[project.optional-dependencies]
dev = ["pytest"]
""",
        encoding="utf-8",
    )
    names = deps.direct_dependencies(tmp_path)
    assert names == ["httpx", "pydantic", "pytest"]


def test_direct_dependencies_from_requirements_txt(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# comment\nrequests==2.31.0\nnumpy\n-e ./local\n", encoding="utf-8"
    )
    names = deps.direct_dependencies(tmp_path)
    assert names == ["numpy", "requests"]
