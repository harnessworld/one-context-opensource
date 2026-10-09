"""Focused tests for runtime skill bundle installation."""

from __future__ import annotations

from pathlib import Path

import pytest

from one_context import skills as skills_module
from one_context.skills import install_skills, uninstall_skills


@pytest.fixture()
def runtime_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / "runtime-skills"
    monkeypatch.setitem(
        skills_module._TOOL_SKILLS_HOME,
        "claude",
        lambda: runtime,
    )
    monkeypatch.setitem(
        skills_module._TOOL_SKILLS_HOME,
        "codex",
        lambda: runtime,
    )
    return runtime


def _skill_dir(root: Path, name: str = "bundle-skill") -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bundle-skill\ndescription: test\n---\n# Bundle\n",
        encoding="utf-8",
    )
    return skill_dir


def test_install_copies_allowlisted_nested_resources_only(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    root = tmp_path / "project"
    skill_dir = _skill_dir(root)

    resources = {
        "references/guides/nested.md": b"nested reference\n",
        "playbooks/publish.md": b"publish steps\n",
        "scripts/run.sh": b"#!/bin/sh\necho ok\n",
        "assets/icons/logo.bin": b"\x00\x01asset",
        "agents/reviewer.md": b"review instructions\n",
        "references/secrets-management.md": b"safe documentation\n",
    }
    for relative, content in resources.items():
        path = skill_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    # Non-bundle top-level content and hidden descendants must stay local.
    excluded = {
        "cache/result.json": b"cache",
        "secret.txt": b"secret",
        "local.env": b"TOKEN=x",
        "README.md": b"notes",
        ".env": b"TOKEN=x",
        "references/.private.md": b"private",
        "references/guides/.cache/result": b"cache",
        "scripts/.local/run.sh": b"local",
        "scripts/__pycache__/run.cpython-312.pyc": b"bytecode",
        "assets/cache/render.bin": b"cache",
        "references/api.env": b"TOKEN=x",
        "assets/client.pem": b"private key",
        "scripts/signing.key": b"private key",
        "agents/auth.p12": b"certificate",
        "playbooks/client.pfx": b"certificate",
        "references/credentials.json": b"{\"token\": \"x\"}",
        "references/service-account-prod.json": b"{\"token\": \"x\"}",
        "references/token.json": b"{\"token\": \"x\"}",
        "references/id_ecdsa": b"private key",
        "references/client.pem.bak": b"private key",
        "references/renamed-key.dat": b"-----BEGIN OPENSSH PRIVATE KEY-----\nkey",
    }
    for relative, content in excluded.items():
        path = skill_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    result = install_skills(root, ["bundle-skill"])

    assert result.installed == ["bundle-skill"]
    assert result.errors == []
    installed = runtime_home / "bundle-skill"
    for relative, content in resources.items():
        assert (installed / relative).read_bytes() == content
    for relative in excluded:
        assert not (installed / relative).exists()
    assert {path.name for path in installed.iterdir()} == {
        "SKILL.md", "references", "playbooks", "scripts", "assets", "agents",
    }


def test_codex_sanitises_only_root_skill_frontmatter(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    root = tmp_path / "project"
    skill_dir = _skill_dir(root)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: bundle-skill\n"
        "description: test\n"
        "triggers: [bundle]\n"
        "---\n"
        "# Bundle\n",
        encoding="utf-8",
    )
    reference = skill_dir / "references" / "schema.md"
    reference.parent.mkdir(parents=True)
    reference_bytes = (
        b"---\nname: resource\ntriggers: [keep-me]\nshort: keep-too\n---\n"
        b"resource body\n"
    )
    reference.write_bytes(reference_bytes)

    result = install_skills(root, ["bundle-skill"], tool="codex")

    assert result.installed == ["bundle-skill"]
    installed = runtime_home / "bundle-skill"
    root_content = (installed / "SKILL.md").read_text(encoding="utf-8")
    assert "name: bundle-skill" in root_content
    assert "triggers:" not in root_content
    assert (installed / "references" / "schema.md").read_bytes() == reference_bytes


def test_install_skips_symlinked_nested_resource(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    root = tmp_path / "project"
    skill_dir = _skill_dir(root)
    references = skill_dir / "references"
    references.mkdir()
    external = tmp_path / "external-secret.md"
    external.write_text("do not copy\n", encoding="utf-8")
    linked = references / "linked.md"
    try:
        linked.symlink_to(external)
    except OSError as exc:  # pragma: no cover - Windows without symlink rights
        pytest.skip(f"symlinks unavailable: {exc}")

    result = install_skills(root, ["bundle-skill"])

    assert result.installed == ["bundle-skill"]
    assert not (runtime_home / "bundle-skill" / "references" / "linked.md").exists()


def test_force_replaces_bundle_and_removes_stale_resources(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    root = tmp_path / "project"
    skill_dir = _skill_dir(root)
    old_reference = skill_dir / "references" / "old.md"
    old_reference.parent.mkdir(parents=True)
    old_reference.write_text("old source\n", encoding="utf-8")

    first = install_skills(root, ["bundle-skill"])
    assert first.installed == ["bundle-skill"]
    installed = runtime_home / "bundle-skill"
    (installed / "references" / "runtime-stale.md").write_text(
        "stale\n", encoding="utf-8",
    )

    old_reference.unlink()
    (skill_dir / "references" / "new.md").write_text(
        "new source\n", encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bundle-skill\n---\n# Updated\n",
        encoding="utf-8",
    )

    skipped = install_skills(root, ["bundle-skill"])
    assert skipped.skipped == ["bundle-skill"]
    assert (installed / "references" / "old.md").is_file()
    assert not (installed / "references" / "new.md").exists()

    replaced = install_skills(root, ["bundle-skill"], force=True)

    assert replaced.installed == ["bundle-skill"]
    assert replaced.errors == []
    assert "# Updated" in (installed / "SKILL.md").read_text(encoding="utf-8")
    assert (installed / "references" / "new.md").read_text(encoding="utf-8") == "new source\n"
    assert not (installed / "references" / "old.md").exists()
    assert not (installed / "references" / "runtime-stale.md").exists()
    assert list(runtime_home.glob(".onecxt-skill-backup-*")) == []


def test_install_rejects_traversal_name_without_touching_external_bundle(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "victim"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# replacement\n", encoding="utf-8")
    external = runtime_home.parent / "victim"
    external.mkdir()
    sentinel = external / "SKILL.md"
    sentinel.write_text("# keep\n", encoding="utf-8")

    result = install_skills(root, ["../victim"], force=True)

    assert result.installed == []
    assert result.errors == [
        "../victim: invalid skill name (expected one portable path component)"
    ]
    assert sentinel.read_text(encoding="utf-8") == "# keep\n"


def test_uninstall_rejects_traversal_name_without_deleting_external_bundle(
    runtime_home: Path,
) -> None:
    external = runtime_home.parent / "victim"
    external.mkdir()
    sentinel = external / "SKILL.md"
    sentinel.write_text("# keep\n", encoding="utf-8")

    result = uninstall_skills(["../victim"])

    assert result.removed == []
    assert result.skipped == ["../victim"]
    assert sentinel.read_text(encoding="utf-8") == "# keep\n"


def test_uninstall_refuses_symlinked_runtime_bundle(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    external = tmp_path / "external-skill"
    external.mkdir()
    sentinel = external / "SKILL.md"
    sentinel.write_text("# keep\n", encoding="utf-8")
    linked = runtime_home / "linked-skill"
    runtime_home.mkdir(parents=True)
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows without symlink rights
        pytest.skip(f"symlinks unavailable: {exc}")

    result = uninstall_skills(["linked-skill"])

    assert result.removed == []
    assert result.skipped == ["linked-skill"]
    assert linked.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "# keep\n"


def test_install_rejects_symlinked_source_bundle(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external-skill"
    external.mkdir()
    (external / "SKILL.md").write_text("# external\n", encoding="utf-8")
    references = external / "references"
    references.mkdir()
    (references / "secret.md").write_text("secret\n", encoding="utf-8")
    source = root / "skills" / "linked-skill"
    source.parent.mkdir(parents=True)
    try:
        source.symlink_to(external, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows without symlink rights
        pytest.skip(f"symlinks unavailable: {exc}")

    result = install_skills(root, ["linked-skill"])

    assert result.installed == []
    assert result.errors == [
        "linked-skill: source skill bundle may not be a symlink"
    ]
    assert not (runtime_home / "linked-skill").exists()


def test_force_cleanup_error_does_not_misreport_committed_install(
    tmp_path: Path,
    runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    skill_dir = _skill_dir(root)
    assert install_skills(root, ["bundle-skill"]).installed == ["bundle-skill"]
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bundle-skill\n---\n# Updated\n",
        encoding="utf-8",
    )

    real_rmtree = skills_module.shutil.rmtree

    def fail_backup_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path).name.startswith(".onecxt-skill-backup-"):
            raise PermissionError("injected backup cleanup failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(skills_module.shutil, "rmtree", fail_backup_cleanup)

    result = install_skills(root, ["bundle-skill"], force=True)

    assert result.installed == ["bundle-skill"]
    assert result.errors == []
    installed = runtime_home / "bundle-skill" / "SKILL.md"
    assert "# Updated" in installed.read_text(encoding="utf-8")


def test_uninstall_still_refuses_non_skill_directory(
    tmp_path: Path,
    runtime_home: Path,
) -> None:
    unrelated = runtime_home / "unrelated"
    unrelated.mkdir(parents=True)
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    result = uninstall_skills(["unrelated"])

    assert result.removed == []
    assert result.skipped == ["unrelated"]
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
