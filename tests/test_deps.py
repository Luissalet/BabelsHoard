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
    # the eager dependency job skips development-only groups (usability report A1)
    assert deps.direct_dependencies(tmp_path) == ["httpx", "pydantic"]
    assert deps.direct_dependencies(tmp_path, include_dev=True) == ["httpx", "pydantic", "pytest"]


def test_direct_dependencies_from_requirements_txt(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# comment\nrequests==2.31.0\nnumpy\n-e ./local\n", encoding="utf-8"
    )
    names = deps.direct_dependencies(tmp_path)
    assert names == ["numpy", "requests"]


def test_poetry_and_dependency_groups(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.poetry.dependencies]
python = "^3.11"
Django = "^5.0"
requests = { version = "^2.31", extras = ["socks"] }

[tool.poetry.group.dev.dependencies]
pytest = "^8"

[dependency-groups]
lint = ["ruff>=0.5", {include-group = "dev"}]
""",
        encoding="utf-8",
    )
    assert deps.direct_dependencies(tmp_path) == ["django", "requests"]
    assert deps.direct_dependencies(tmp_path, include_dev=True) == ["django", "pytest", "requests", "ruff"]
