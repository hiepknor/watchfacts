from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


Complete = Callable[[str], Awaitable[str]]
LoadAuditArtifact = Callable[[Any], Any]
SummarizeArtifact = Callable[[Any], Any]
RenderReport = Callable[..., str]
RunAiTriage = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class AuditTriageUseCase:
    load_artifact: LoadAuditArtifact
    summarize_artifact: SummarizeArtifact
    render_markdown_report: RenderReport
    render_json_report: RenderReport
    run_ai_triage: RunAiTriage

    def load(self, path: Any) -> Any:
        return self.load_artifact(path)

    def summarize(self, artifact: Any) -> Any:
        return self.summarize_artifact(artifact)

    def render_markdown(self, artifact: Any, *, ai_report: Any | None) -> str:
        return self.render_markdown_report(artifact, ai_report=ai_report)

    def render_json(self, artifact: Any, *, ai_report: Any | None) -> str:
        return self.render_json_report(artifact, ai_report=ai_report)

    async def run_ai(
        self,
        artifact: Any,
        *,
        complete: Complete,
        max_rows: int,
    ) -> Any:
        return await self.run_ai_triage(
            artifact,
            complete=complete,
            max_rows=max_rows,
        )
