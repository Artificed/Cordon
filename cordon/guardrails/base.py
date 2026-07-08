"""Shared verdict type for guardrail layers.

Every layer is a pure function over (plan | input text, context) returning a
LayerVerdict. Severity order for plan disposition: block > requires_approval
> pass — the worst per-action outcome decides the whole plan (fail-closed).
"""

from pydantic import BaseModel

PASS = "pass"
BLOCK = "block"
REQUIRES_APPROVAL = "requires_approval"

_SEVERITY = {PASS: 0, REQUIRES_APPROVAL: 1, BLOCK: 2}


class LayerVerdict(BaseModel):
    outcome: str = PASS
    reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.outcome == BLOCK

    @property
    def passed(self) -> bool:
        return self.outcome == PASS


def worst(verdicts: list[LayerVerdict]) -> LayerVerdict:
    return max(verdicts, key=lambda v: _SEVERITY[v.outcome], default=LayerVerdict())
