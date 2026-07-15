"""Concrete stage modules for the p2m pipeline."""

from __future__ import annotations

from . import design, judge, policy, rollout, seeds, systematization, systematization_convert

STAGES = {
    "policy": policy,
    "design": design,
    "seeds": seeds,
    "rollout": rollout,
    "judge": judge,
    "systematization": systematization,
    "systematization_convert": systematization_convert,
}

STAGE_NAMES = tuple(STAGES)

__all__ = ["STAGES", "STAGE_NAMES"]
