from __future__ import annotations


class ManifestError(Exception):
    """Invalid or inconsistent YAML manifests under meta/."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SkillsSubmoduleNotInitialized(RuntimeError):
    """skills/ is registered as a git submodule but its contents are missing.

    Raised by ``discover_skills`` when ``.gitmodules`` declares ``skills`` yet
    the working tree's ``skills/`` directory holds no ``SKILL.md`` files — the
    classic signature of ``git submodule update --init`` not having been run
    after a fresh clone.  Failing loudly here stops ``onecxt adapt`` from
    silently emitting empty routing tables / skill registrations that would
    otherwise drift into commits.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "skills/ is declared as a git submodule in .gitmodules but "
                "contains no SKILL.md files. "
                "Run: git submodule update --init --recursive skills"
            )
        )
