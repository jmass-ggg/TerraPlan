"""Cycle-level CP-SAT optimizer for the annual crop planner.

Suitability is supplied by ``crop_engine``.  This module only selects a
calendar-feasible combination of already-scored crop cycles.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

from ortools.sat.python import cp_model

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CropCycleCandidate:
    crop_name: str
    category: str
    family: str
    start_month: int
    duration_months: int
    occupied_months: tuple[int, ...]
    recovery_months: tuple[int, ...]
    harvest_month: int | None
    suitability_index: int
    limiting_factor: str
    reason: str
    data_mode: str
    snapshot_id: str | None
    continues_next_year: bool = False
    preferred_after: tuple[str, ...] = ()
    avoid_after: tuple[str, ...] = ()

    @property
    def reserved_months(self) -> tuple[int, ...]:
        return self.occupied_months + self.recovery_months

    @property
    def last_reserved_month(self) -> int:
        return max(self.reserved_months, default=self.start_month)


@dataclass(frozen=True)
class OptimizationResult:
    selected_cycles: tuple[CropCycleCandidate, ...]
    solver_status: str
    solve_ms: int
    fallback_used: bool = False


def transition_value(previous: CropCycleCandidate, current: CropCycleCandidate) -> int:
    """Return a small transition value; suitability remains dominant."""
    if previous.crop_name == current.crop_name:
        return -1_200
    if previous.crop_name in current.avoid_after:
        return -900
    if previous.crop_name in current.preferred_after:
        return 1_200
    if previous.family != "unknown" and previous.family == current.family:
        return -600
    if previous.family != current.family:
        return 300
    return 0


class CropPlanOptimizer:
    """Select non-overlapping crop cycles with a short deterministic solve."""

    def __init__(self, max_solve_seconds: float = 0.75) -> None:
        self.max_solve_seconds = max_solve_seconds

    def optimize(
        self,
        candidates: Iterable[CropCycleCandidate],
    ) -> OptimizationResult:
        candidate_list = sorted(
            candidates,
            key=lambda c: (c.start_month, c.crop_name, c.duration_months),
        )
        started = time.perf_counter()
        if not candidate_list:
            return OptimizationResult((), "EMPTY", 0)

        model = cp_model.CpModel()
        selected = [model.new_bool_var(f"cycle_{i}") for i in range(len(candidate_list))]

        for month in range(1, 13):
            occupying = [
                selected[i]
                for i, cycle in enumerate(candidate_list)
                if month in cycle.reserved_months
            ]
            if occupying:
                model.add(sum(occupying) <= 1)

        objective_terms = []
        for i, cycle in enumerate(candidate_list):
            # Score every occupied growing month. This prevents short crops from
            # winning merely because more cycles fit in a year.
            base_value = cycle.suitability_index * len(cycle.occupied_months) * 100
            coverage_value = len(cycle.occupied_months) * 25
            deterministic_tie_break = max(0, 20 - i)
            objective_terms.append(
                selected[i] * (base_value + coverage_value + deterministic_tie_break)
            )

        # Transition variables are defined only for truly adjacent crop cycles,
        # never for successive occupancy months inside one cycle.
        for i, previous in enumerate(candidate_list):
            for j, current in enumerate(candidate_list):
                if current.start_month != previous.last_reserved_month + 1:
                    continue
                value = transition_value(previous, current)
                if value == 0:
                    continue
                pair = model.new_bool_var(f"transition_{i}_{j}")
                model.add(pair <= selected[i])
                model.add(pair <= selected[j])
                model.add(pair >= selected[i] + selected[j] - 1)
                objective_terms.append(pair * value)

        model.maximize(sum(objective_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.max_solve_seconds
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        status = solver.solve(model)
        status_name = solver.status_name(status)
        solve_ms = round((time.perf_counter() - started) * 1_000)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return OptimizationResult((), status_name, solve_ms)

        cycles = tuple(
            cycle
            for variable, cycle in zip(selected, candidate_list, strict=True)
            if solver.boolean_value(variable)
        )
        return OptimizationResult(
            tuple(sorted(cycles, key=lambda c: (c.start_month, c.crop_name))),
            status_name,
            solve_ms,
        )


def deterministic_fallback(
    candidates: Iterable[CropCycleCandidate],
) -> OptimizationResult:
    """Greedy, occupancy-safe fallback used when CP-SAT cannot return a plan."""
    candidate_list = list(candidates)
    selected: list[CropCycleCandidate] = []
    reserved: set[int] = set()
    previous: CropCycleCandidate | None = None

    for month in range(1, 13):
        if month in reserved:
            continue
        options = [
            cycle
            for cycle in candidate_list
            if cycle.start_month == month and not reserved.intersection(cycle.reserved_months)
        ]
        if not options:
            continue
        options.sort(
            key=lambda cycle: (
                cycle.suitability_index * 10
                + (transition_value(previous, cycle) // 100 if previous else 0),
                cycle.suitability_index,
                cycle.crop_name,
            ),
            reverse=True,
        )
        chosen = options[0]
        selected.append(chosen)
        reserved.update(chosen.reserved_months)
        previous = chosen

    return OptimizationResult(
        tuple(selected),
        "FALLBACK",
        0,
        fallback_used=True,
    )
