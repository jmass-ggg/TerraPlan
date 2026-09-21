from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from app.domain.crop_register import CROP_REGISTER
from app.services import planner_service
from app.services.planner_optimizer import (
    CropCycleCandidate,
    CropPlanOptimizer,
    deterministic_fallback,
)


def _cycle(
    crop: str,
    start: int,
    duration: int,
    score: int,
    *,
    family: str = "poaceae",
    preferred_after: tuple[str, ...] = (),
) -> CropCycleCandidate:
    occupied = tuple(month for month in range(start, start + duration) if month <= 12)
    harvest = start + duration - 1
    return CropCycleCandidate(
        crop_name=crop,
        category="cereal",
        family=family,
        start_month=start,
        duration_months=duration,
        occupied_months=occupied,
        recovery_months=(),
        harvest_month=harvest if harvest <= 12 else None,
        suitability_index=score,
        limiting_factor="water",
        reason="Deterministic crop-engine result",
        data_mode="demonstration",
        snapshot_id=None,
        continues_next_year=harvest > 12,
        preferred_after=preferred_after,
    )


def test_solver_returns_valid_non_overlapping_plan():
    result = CropPlanOptimizer().optimize([
        _cycle("Wheat", 1, 5, 90),
        _cycle("Maize", 3, 4, 95),
        _cycle("Soybean", 6, 4, 87, family="fabaceae", preferred_after=("Wheat",)),
    ])

    assert result.solver_status in {"OPTIMAL", "FEASIBLE"}
    occupied = [month for cycle in result.selected_cycles for month in cycle.reserved_months]
    assert len(occupied) == len(set(occupied))


def test_crop_duration_reserves_every_occupied_month():
    wheat = _cycle("Wheat", 1, 5, 90)
    february = _cycle("Maize", 2, 4, 99)
    result = CropPlanOptimizer().optimize([wheat, february])

    assert len(result.selected_cycles) == 1
    assert not ({1, 2, 3, 4, 5}.issubset(set(result.selected_cycles[0].occupied_months))
                and result.selected_cycles[0].crop_name != "Wheat")


def test_rotation_diversity_can_break_close_scores():
    result = CropPlanOptimizer().optimize([
        _cycle("Wheat", 1, 5, 90),
        _cycle("Wheat", 6, 4, 89),
        _cycle("Soybean", 6, 4, 87, family="fabaceae", preferred_after=("Wheat",)),
    ])

    assert [cycle.crop_name for cycle in result.selected_cycles] == ["Wheat", "Soybean"]


def test_suitability_dominates_large_diversity_gap():
    result = CropPlanOptimizer().optimize([
        _cycle("Wheat", 1, 5, 95),
        _cycle("Wheat", 6, 4, 94),
        _cycle("Soybean", 6, 4, 40, family="fabaceae", preferred_after=("Wheat",)),
    ])

    assert [cycle.crop_name for cycle in result.selected_cycles] == ["Wheat", "Wheat"]


def test_repetition_is_between_cycles_not_occupancy_months():
    result = CropPlanOptimizer().optimize([_cycle("Wheat", 1, 5, 90)])
    assert len(result.selected_cycles) == 1
    assert result.selected_cycles[0].occupied_months == (1, 2, 3, 4, 5)


def test_deterministic_fallback_is_occupancy_safe():
    result = deterministic_fallback([
        _cycle("Wheat", 1, 5, 90),
        _cycle("Maize", 3, 4, 99),
        _cycle("Soybean", 6, 4, 85, family="fabaceae"),
    ])
    occupied = [month for cycle in result.selected_cycles for month in cycle.reserved_months]
    assert result.fallback_used
    assert len(occupied) == len(set(occupied))


def test_candidate_generation_respects_saved_and_past_months(monkeypatch):
    wheat = next(crop for crop in CROP_REGISTER if crop.name == "Wheat")
    monkeypatch.setattr(
        planner_service,
        "score",
        lambda crop, context: SimpleNamespace(
            suitability_index=90,
            hard_exclusion=False,
            limiting_factor="water",
            reason="Strong match",
            label="Good match",
        ),
    )
    context_for = lambda month, crop: SimpleNamespace(data_mode="demonstration", snapshot_id=None)

    candidates, _ = planner_service._build_cycle_candidates(
        (wheat,), context_for, earliest_start_month=7, blocked_months={9}
    )

    assert candidates
    assert all(candidate.start_month >= 7 for candidate in candidates)
    assert all(9 not in candidate.reserved_months for candidate in candidates)


def test_future_year_candidates_can_start_in_january(monkeypatch):
    maize = next(crop for crop in CROP_REGISTER if crop.name == "Maize")
    monkeypatch.setattr(
        planner_service,
        "score",
        lambda crop, context: SimpleNamespace(
            suitability_index=82,
            hard_exclusion=False,
            limiting_factor="water",
            reason="Strong match",
            label="Good match",
        ),
    )
    context_for = lambda month, crop: SimpleNamespace(data_mode="demonstration", snapshot_id=None)
    candidates, _ = planner_service._build_cycle_candidates(
        (maize,), context_for, earliest_start_month=1, blocked_months=set()
    )
    assert any(candidate.start_month == 1 for candidate in candidates)


def test_cross_year_cycle_is_preserved():
    cycle = _cycle("Pineapple", 10, 18, 80, family="bromeliaceae")
    result = CropPlanOptimizer().optimize([cycle])
    assert result.selected_cycles[0].continues_next_year
    assert result.selected_cycles[0].harvest_month is None
    assert result.selected_cycles[0].occupied_months == (10, 11, 12)
