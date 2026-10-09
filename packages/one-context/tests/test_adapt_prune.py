"""Tests for orphan generated-skill file pruning (``_prune_orphan_skill_files``)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from one_context.adapters._shared_rules import (
    GENERATED_HEADER_MD,
)
from one_context.cache import file_hash, load_cache, save_cache
from one_context.cli import _adapt
from one_context.cli._adapt import (
    _adapter_names_for_run,
    _evict_legacy_cache_entries,
    _evict_skipped_adapter_cache_entries,
    _orphan_agent_blockers,
    _orphan_pi_skill_alias_blockers,
    _orphan_skill_blockers,
    _prune_legacy_gen_files,
    _prune_orphan_agent_files,
    _prune_orphan_pi_skill_alias_files,
    _prune_orphan_skill_files,
    _prune_skipped_adapter_files,
)


_SURFACE_PATHS = {
    "claude": ".claude/skills/{name}/SKILL.md",
    "cursor": ".cursor/rules/skill-{name}.mdc",
    "hermes": ".hermes/skills/{name}.md",
    "opencode": ".opencode/skills/{name}/SKILL.md",
    "agents": ".agents/skills/{name}/SKILL.md",
}

_AGENT_SURFACE_PATHS = {
    "claude": ".claude/agents/{name}.md",
    "cursor": ".cursor/rules/agent-{name}.mdc",
    "hermes": ".hermes/agents/{name}.md",
    "opencode": ".opencode/agent/{name}.md",
    "codex": ".codex/agents/{name}.toml",
    "pi": ".pi/prompts/agent-{name}.md",
}


def _surface_path(surface: str, name: str) -> str:
    return _SURFACE_PATHS[surface].format(name=name)


def _plant_orphan(root: Path, surface: str, name: str) -> Path:
    """Write a generated-marker skill file for a non-existent skill ``name``."""
    f = root / _surface_path(surface, name)
    f.parent.mkdir(parents=True, exist_ok=True)
    if surface == "cursor":
        f.write_text("---\n---\n" + GENERATED_HEADER_MD + "\n\norphan\n", encoding="utf-8")
        return f
    f.write_text(GENERATED_HEADER_MD + "\n\norphan\n", encoding="utf-8")
    return f


def test_removes_generated_orphans_across_surfaces(tmp_path: Path) -> None:
    """Orphan generated skill files in every adapter location are removed."""
    files = [
        _plant_orphan(tmp_path, s, "__ghost__")
        for s in _SURFACE_PATHS
    ]

    removed = _prune_orphan_skill_files(tmp_path, active_skill_dirs=set(), dry_run=False)

    assert len(removed) == len(_SURFACE_PATHS)
    for f in files:
        assert not f.exists()
    # The empty .claude/skills/__ghost__ dir is dropped too.
    assert not (tmp_path / ".claude" / "skills" / "__ghost__").exists()


def test_keeps_files_for_active_skills(tmp_path: Path) -> None:
    """Files whose skill name is in the active set are left alone."""
    _plant_orphan(tmp_path, "claude", "keep-me")

    removed = _prune_orphan_skill_files(
        tmp_path, active_skill_dirs={"keep-me"}, dry_run=False
    )

    assert removed == []
    assert (tmp_path / ".claude" / "skills" / "keep-me" / "SKILL.md").exists()


def test_never_touches_handwritten_files(tmp_path: Path) -> None:
    """Files without the GENERATED marker are never removed."""
    d = tmp_path / ".claude" / "skills" / "hand"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# hand-written, no marker\n", encoding="utf-8")
    (tmp_path / ".cursor" / "rules").mkdir(parents=True, exist_ok=True)
    hw = tmp_path / ".cursor" / "rules" / "skill-hand.mdc"
    hw.write_text("# hand\n", encoding="utf-8")

    removed = _prune_orphan_skill_files(tmp_path, active_skill_dirs=set(), dry_run=False)

    assert removed == []
    assert (d / "SKILL.md").exists()
    assert hw.exists()


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    """In dry-run mode paths are reported but files stay on disk."""
    f = _plant_orphan(tmp_path, "hermes", "__dry__")

    removed = _prune_orphan_skill_files(
        tmp_path, active_skill_dirs=set(), dry_run=True
    )

    assert removed == [".hermes/skills/__dry__.md"]
    assert f.exists()


def test_removes_generated_orphan_agents_across_surfaces(tmp_path: Path) -> None:
    planted: list[Path] = []
    for pattern in _AGENT_SURFACE_PATHS.values():
        target = tmp_path / pattern.format(name="ghost")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(GENERATED_HEADER_MD + "\n\norphan\n", encoding="utf-8")
        planted.append(target)

    removed = _prune_orphan_agent_files(
        tmp_path, active_agent_ids=set(), dry_run=False
    )

    assert len(removed) == len(_AGENT_SURFACE_PATHS)
    assert all(not target.exists() for target in planted)


def test_keeps_active_and_handwritten_pi_prompts(tmp_path: Path) -> None:
    active = tmp_path / ".pi/prompts/agent-active.md"
    handwritten = tmp_path / ".pi/prompts/agent-hand.md"
    active.parent.mkdir(parents=True)
    active.write_text(GENERATED_HEADER_MD + "\n", encoding="utf-8")
    handwritten.write_text("# hand-written\n", encoding="utf-8")

    removed = _prune_orphan_agent_files(
        tmp_path, active_agent_ids={"active"}, dry_run=False
    )

    assert removed == []
    assert active.exists()
    assert handwritten.exists()


@pytest.mark.parametrize(
    "rel_path",
    [
        ".codex/agent/pm.md",
        ".codex/skills/demo/SKILL.md",
        ".openclaw/agents/pm.json",
        ".openclaw/skills/demo.json",
    ],
)
def test_removes_retired_generated_surfaces_even_for_active_sources(
    tmp_path: Path, rel_path: str
) -> None:
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    content = GENERATED_HEADER_MD + "\n"
    target.write_text(content, encoding="utf-8")
    cache = {rel_path: file_hash(content)}

    removed = _prune_legacy_gen_files(
        tmp_path,
        dry_run=False,
        cache=cache,
    )

    assert rel_path in removed
    assert not target.exists()


def test_evicts_retired_surface_cache_entries(tmp_path: Path) -> None:
    cache = {
        ".codex/skills/demo/SKILL.md": "old",
        ".openclaw/agents/pm.json": "old",
        ".agents/skills/demo/SKILL.md": "current",
    }

    evicted = _evict_legacy_cache_entries(
        tmp_path,
        cache,
        removed_paths={".openclaw/agents/pm.json"},
        dry_run=False,
    )

    assert evicted == [
        ".codex/skills/demo/SKILL.md",
        ".openclaw/agents/pm.json",
    ]
    assert cache == {".agents/skills/demo/SKILL.md": "current"}


def test_retired_modified_or_handwritten_files_are_blocked(tmp_path: Path) -> None:
    from one_context.cli._adapt import _legacy_gen_blockers

    modified = tmp_path / ".codex/skills/demo/SKILL.md"
    handwritten = tmp_path / ".openclaw/agents/pm.json"
    modified.parent.mkdir(parents=True)
    handwritten.parent.mkdir(parents=True)
    original = GENERATED_HEADER_MD + "\noriginal\n"
    modified.write_text(GENERATED_HEADER_MD + "\nuser edit\n", encoding="utf-8")
    handwritten.write_text('{"user": true}\n', encoding="utf-8")
    cache = {modified.relative_to(tmp_path).as_posix(): file_hash(original)}

    assert _prune_legacy_gen_files(
        tmp_path,
        dry_run=False,
        cache=cache,
    ) == []
    assert _legacy_gen_blockers(tmp_path, cache) == [
        ".codex/skills/demo/SKILL.md",
        ".openclaw/agents/pm.json",
    ]
    assert modified.exists()
    assert handwritten.exists()


def test_prune_skips_symlinked_base_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "agent-ghost.md"
    target.write_text(GENERATED_HEADER_MD + "\n", encoding="utf-8")
    pi_dir = repo / ".pi"
    pi_dir.mkdir()
    try:
        (pi_dir / "prompts").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows without symlink rights
        pytest.skip(f"symlinks unavailable: {exc}")

    removed = _prune_orphan_agent_files(
        repo,
        active_agent_ids=set(),
        dry_run=False,
    )

    assert removed == []
    assert target.exists()
    assert _orphan_agent_blockers(repo, set()) == [".pi/prompts"]


def test_prune_skips_nested_symlink_inside_owned_base(tmp_path: Path) -> None:
    base = tmp_path / ".claude/skills"
    active = base / "active"
    active.mkdir(parents=True)
    target = active / "SKILL.md"
    target.write_text(GENERATED_HEADER_MD + "\n", encoding="utf-8")
    try:
        (base / "orphan").symlink_to(active, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows without symlink rights
        pytest.skip(f"symlinks unavailable: {exc}")

    removed = _prune_orphan_skill_files(
        tmp_path,
        active_skill_dirs={"active"},
        dry_run=False,
    )

    # The real active path can be selected separately, but the symlinked alias
    # must never cause a second delete outside its lexical ownership path.
    assert removed == []
    assert target.exists()
    assert _orphan_skill_blockers(tmp_path, {"active"}) == [
        ".claude/skills/orphan/SKILL.md"
    ]


def test_legacy_cleanup_skips_intermediate_symlink_outside_repo(
    tmp_path: Path,
) -> None:
    from one_context.cli._adapt import _legacy_gen_blockers

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    empty_child = outside / "skills" / "empty-child"
    empty_child.mkdir(parents=True)
    try:
        (repo / ".codex").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows without symlink rights
        pytest.skip(f"symlinks unavailable: {exc}")

    assert _prune_legacy_gen_files(
        repo,
        dry_run=False,
        cache={},
    ) == []
    assert (outside / "skills").is_dir()
    assert empty_child.is_dir()
    assert _legacy_gen_blockers(repo, {}) == [".codex/skills"]


class _NoopAdapter:
    def generate(self, root: Path, workspace: dict, context: dict) -> list:
        return []

    def generate_skills(self, root: Path, skills: list) -> list:
        return []

    def generate_project_artifacts(
        self, root: Path, workspace_ids: list[str], agents: list
    ) -> list:
        return []

    def unsupported_assets(self) -> list:
        return []


def _stub_adapt_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep cache-prune integration tests independent of adapter generation."""
    import one_context.adapters as adapters
    import one_context.adapters._report as report
    import one_context.project_artifacts as project_artifacts
    import one_context.skills as skills

    monkeypatch.setattr(_adapt, "load_agents", lambda root: ([], []))
    monkeypatch.setattr(_adapt, "load_profiles", lambda root: ([], {}))
    monkeypatch.setattr(_adapt, "load_mixins", lambda root: ([], {}))
    monkeypatch.setattr(
        _adapt,
        "build_workspace_context",
        lambda root, workspace_id: {"workspace": {"id": workspace_id}},
    )
    monkeypatch.setattr(adapters, "get_adapter", lambda name: _NoopAdapter())
    monkeypatch.setattr(
        skills,
        "discover_skills",
        lambda root: [SimpleNamespace(dir_name="keep")],
    )
    monkeypatch.setattr(project_artifacts, "build_agents_md", lambda root, found: "agents\n")
    monkeypatch.setattr(report, "save_report", lambda root, value: None)
    monkeypatch.setattr(
        report,
        "probe_cli",
        lambda name: SimpleNamespace(available=True, error=None, to_dict=lambda: {}),
    )


def _adapt_args(*, dry_run: bool) -> SimpleNamespace:
    return SimpleNamespace(
        all=False,
        workspace_id="dev",
        only="cursor",
        dry_run=dry_run,
        check=False,
        force=False,
    )


def test_adapt_evicts_missing_orphan_cache_keys_across_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache-only orphans are evicted for every known adapter skill surface."""
    _stub_adapt_dependencies(monkeypatch)
    orphan_paths = {_surface_path(surface, "ghost") for surface in _SURFACE_PATHS}
    active_paths = {_surface_path(surface, "keep") for surface in _SURFACE_PATHS}
    original = {
        **{path: "orphan-hash" for path in orphan_paths},
        **{path: "active-hash" for path in active_paths},
        "unrelated/generated.md": "keep-hash",
    }
    save_cache(tmp_path, original)

    assert _adapt._cmd_adapt(tmp_path, _adapt_args(dry_run=False)) == 0

    persisted = load_cache(tmp_path)
    assert orphan_paths.isdisjoint(persisted)
    assert active_paths.issubset(persisted)
    assert {persisted[path] for path in active_paths} == {"active-hash"}
    assert persisted["unrelated/generated.md"] == "keep-hash"


def test_adapt_dry_run_does_not_evict_orphan_cache_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run reports/prunes no cache state, including cache-only orphans."""
    _stub_adapt_dependencies(monkeypatch)
    original = {
        _surface_path(surface, "ghost"): "orphan-hash"
        for surface in _SURFACE_PATHS
    }
    original["unrelated/generated.md"] = "keep-hash"
    save_cache(tmp_path, original)

    assert _adapt._cmd_adapt(tmp_path, _adapt_args(dry_run=True)) == 0

    assert load_cache(tmp_path) == original


def _plant_pi_skill_alias_orphan(root: Path, skill_dir: str, alias: str) -> Path:
    """Write a generated pi alias prompt for a (skill, alias) pair."""
    f = root / ".pi/prompts" / f"{alias}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "---\n"
        "description: orphan alias\n"
        "argument-hint: '[args]'\n"
        "---\n\n"
        f"{GENERATED_HEADER_MD}\n\n"
        f"<!-- canonical-source: skills/{skill_dir}/SKILL.md; alias: {alias} -->\n"
        f"# Activate skill: {skill_dir}\n",
        encoding="utf-8",
    )
    return f


def _plant_pi_agent_prompt(root: Path, agent_id: str) -> Path:
    """Write a generated pi agent prompt that must NOT be touched by the alias pruner."""
    f = root / ".pi/prompts" / f"agent-{agent_id}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "---\n---\n\n"
        f"{GENERATED_HEADER_MD}\n\nagent orphan\n",
        encoding="utf-8",
    )
    return f


def _plant_handwritten_pi_prompt(root: Path, name: str) -> Path:
    """Write a hand-written (no marker) pi prompt that must never be pruned."""
    f = root / ".pi/prompts" / f"{name}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# My hand-written prompt\n\nDo not touch me.\n", encoding="utf-8")
    return f


class TestPiSkillAliasPrune:
    def test_removes_alias_prompt_whose_source_alias_was_removed(self, tmp_path):
        _plant_pi_skill_alias_orphan(tmp_path, "sc", "sc")

        removed = _prune_orphan_pi_skill_alias_files(
            tmp_path, active_aliases=set(), dry_run=False
        )

        assert removed == [".pi/prompts/sc.md"]
        assert not (tmp_path / ".pi/prompts/sc.md").exists()

    def test_keeps_alias_prompt_still_declared(self, tmp_path):
        _plant_pi_skill_alias_orphan(tmp_path, "sc", "sc")

        removed = _prune_orphan_pi_skill_alias_files(
            tmp_path, active_aliases={("sc", "sc")}, dry_run=False
        )

        assert removed == []
        assert (tmp_path / ".pi/prompts/sc.md").is_file()

    def test_does_not_touch_agent_prompts_or_handwritten(self, tmp_path):
        _plant_pi_skill_alias_orphan(tmp_path, "sc", "sc")
        agent = _plant_pi_agent_prompt(tmp_path, "pm")
        handwritten = _plant_handwritten_pi_prompt(tmp_path, "custom")

        removed = _prune_orphan_pi_skill_alias_files(
            tmp_path, active_aliases=set(), dry_run=False
        )

        assert removed == [".pi/prompts/sc.md"]
        assert agent.is_file()
        assert handwritten.is_file()

    def test_dry_run_reports_without_removing(self, tmp_path):
        f = _plant_pi_skill_alias_orphan(tmp_path, "gs", "gs")

        removed = _prune_orphan_pi_skill_alias_files(
            tmp_path, active_aliases=set(), dry_run=True
        )

        assert removed == [".pi/prompts/gs.md"]
        assert f.is_file()

    def test_blockers_report_stale_alias_without_removing(self, tmp_path):
        _plant_pi_skill_alias_orphan(tmp_path, "sc", "sc")

        blocked = _orphan_pi_skill_alias_blockers(tmp_path, active_aliases=set())

        assert blocked == [".pi/prompts/sc.md"]
        assert (tmp_path / ".pi/prompts/sc.md").is_file()

    def test_blockers_empty_when_no_stale_aliases(self, tmp_path):
        _plant_pi_skill_alias_orphan(tmp_path, "sc", "sc")

        blocked = _orphan_pi_skill_alias_blockers(
            tmp_path, active_aliases={("sc", "sc")}
        )

        assert blocked == []


def test_default_adapt_skips_cursor() -> None:
    names = _adapter_names_for_run(
        ["claude_code", "codex", "cursor", "pi"], only=None
    )
    assert "cursor" not in names
    assert _adapter_names_for_run(["cursor"], only="cursor") == ["cursor"]


def test_prunes_generated_cursor_rules_when_skipped(tmp_path: Path) -> None:
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    generated = rules / "skill-demo.mdc"
    generated.write_text("---\n---\n" + GENERATED_HEADER_MD + "\n", encoding="utf-8")
    handwritten = rules / "local.mdc"
    handwritten.write_text("keep me\n", encoding="utf-8")

    removed = _prune_skipped_adapter_files(
        tmp_path, adapter_names=["pi"], dry_run=False
    )

    assert removed == [".cursor/rules/skill-demo.mdc"]
    assert not generated.exists()
    assert handwritten.is_file()


def test_keeps_cursor_rules_when_cursor_is_active(tmp_path: Path) -> None:
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    generated = rules / "skill-demo.mdc"
    generated.write_text("---\n---\n" + GENERATED_HEADER_MD + "\n", encoding="utf-8")

    removed = _prune_skipped_adapter_files(
        tmp_path, adapter_names=["cursor"], dry_run=False
    )

    assert removed == []
    assert generated.is_file()


def test_evicts_skipped_cursor_cache_keys(tmp_path: Path) -> None:
    cache = {
        ".cursor/rules/skill-demo.mdc": "h1",
        ".claude/skills/demo/SKILL.md": "h2",
    }
    evicted = _evict_skipped_adapter_cache_entries(
        cache, adapter_names=["pi"], dry_run=False
    )
    assert evicted == [".cursor/rules/skill-demo.mdc"]
    assert ".claude/skills/demo/SKILL.md" in cache
