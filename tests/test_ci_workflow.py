from __future__ import annotations

from pathlib import Path


def test_ci_whitespace_check_uses_commit_ranges() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    whitespace_step = workflow.split("      - name: Check diff whitespace", 1)[
        1
    ].split("      - name: Run tests", 1)[0]

    assert "fetch-depth: 0" in workflow
    assert 'TARGET_SHA="${GITHUB_SHA:-HEAD}"' in whitespace_step
    assert 'git diff --check "${BASE_SHA}...${TARGET_SHA}"' in whitespace_step
    assert 'git diff --check "${BEFORE_SHA}..${TARGET_SHA}"' in whitespace_step
    assert 'git diff --check "${PARENT_SHA}..${TARGET_SHA}"' in whitespace_step
    assert "git diff-tree --check --no-commit-id -r \"${TARGET_SHA}\"" in whitespace_step
