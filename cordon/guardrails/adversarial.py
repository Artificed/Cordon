"""Adversarial-input check (layer 0).

A documented pattern list over the intent text and over resource metadata
notes (the indirect channel). The patterns instantiate the injection
families from the prompt-injection literature: instruction-override
wrappers, claimed pre-authorization, and guardrail-disabling language.

This layer is deliberately heuristic and is only the first line of
defense-in-depth: when a novel phrasing slips past it, the capability,
policy, and risk layers still block the *payload*.
"""

from cordon.guardrails.base import BLOCK, LayerVerdict
from cordon.schema import Resource

INJECTION_PATTERNS = [
    "ignore all previous instructions",
    "ignore previous instructions",
    "ignore all security rules",
    "system override",
    "developer mode",
    "no restrictions",
    "pre-approved by the admin",
    "guardrails disabled",
    "skip all checks",
    "bypass all checks",
    "disable all safety",
]


def check_adversarial(intent: str, resources: list[Resource]) -> LayerVerdict:
    texts = [("intent", intent)]
    for r in resources:
        note = (r.metadata or {}).get("note")
        if note:
            texts.append((f"metadata:{r.resource_id}", str(note)))

    for channel, text in texts:
        lowered = text.lower()
        for pattern in INJECTION_PATTERNS:
            if pattern in lowered:
                return LayerVerdict(outcome=BLOCK,
                                    reason=f"injection_pattern:{channel}:{pattern}")
    return LayerVerdict()
