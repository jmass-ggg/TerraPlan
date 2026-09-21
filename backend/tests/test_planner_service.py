from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from app.domain.crop_register import CROP_REGISTER
from app.services import planner_service


def _crop(name: str):
    return next(crop for crop in CROP_REGISTER if crop.name == name)


def _context(_month, _crop):
    return SimpleNamespace(data_mode="demonstration", snapshot_id=None)


def _result(crop, score=80):
    return SimpleNamespace(
        crop_name=crop.name,
        suitability_index=score,
        hard_exclusion=False,
        limiting_factor="water",
        reason="Environmental suitability explanation.",
        label="Possible match",
    )


def test_occupancy_lifecycle_and_duration(monkeypatch):
    maize = replace(_crop("Maize"), preferred_after=(), avoid_after=())
    monkeypatch.setattr(planner_service, "score", lambda crop, context: _result(crop, 85))

    timeline, _ = planner_service._build_annual_sequence((maize,), _context)

    first_cycle = timeline[:4]
    assert [item.month for item in first_cycle] == [1, 2, 3, 4]
    assert {item.crop_name for item in first_cycle} == {"Maize"}
    assert [item.stage for item in first_cycle] == ["planting", "growing", "maturing", "harvest"]
    assert timeline[4].month == 5
    assert timeline[4].stage == "planting"
    assert planner_service._derive_harvest_date(date(2027, 1, 1), "Maize") == date(2027, 5, 1)


def test_rotation_penalties_and_preferred_bonus():
    maize = _crop("Maize")
    beans = _crop("Beans")

    assert planner_service._rotation_adjustment(maize, maize)[0] == planner_service.SAME_CROP_PENALTY
    adjustment, effect = planner_service._rotation_adjustment(beans, maize)
    assert adjustment == planner_service.PREFERRED_ROTATION_BONUS
    assert effect == "preferred"


def test_preferred_rotation_changes_next_crop(monkeypatch):
    maize = _crop("Maize")
    beans = _crop("Beans")
    monkeypatch.setattr(
        planner_service,
        "score",
        lambda crop, context: _result(crop, 90 if crop.name == "Maize" else 84),
    )

    timeline, _ = planner_service._build_annual_sequence((maize, beans), _context)
    planting = [item for item in timeline if item.stage == "planting"]

    assert planting[0].crop_name == "Maize"
    assert planting[1].crop_name == "Beans"
    assert planting[1].rotation_effect == "preferred"


def test_perennials_are_excluded_from_rotation(monkeypatch):
    maize = _crop("Maize")
    mango = _crop("Mango")
    monkeypatch.setattr(planner_service, "score", lambda crop, context: _result(crop, 95 if crop.name == "Mango" else 75))

    timeline, opportunities = planner_service._build_annual_sequence((maize, mango), _context)

    assert all(item.crop_name != "Mango" for item in timeline)
    assert [item.crop_name for item in opportunities] == ["Mango"]


def test_recovery_months_are_reserved(monkeypatch):
    beans = replace(_crop("Beans"), recovery_months=1)
    monkeypatch.setattr(planner_service, "score", lambda crop, context: _result(crop))

    timeline, _ = planner_service._build_annual_sequence((beans,), _context)

    assert [(item.month, item.stage) for item in timeline[:4]] == [
        (1, "planting"),
        (2, "growing"),
        (3, "harvest"),
        (4, "recovery"),
    ]
    assert timeline[4].month == 5


def test_year_boundary_never_emits_invalid_month(monkeypatch):
    pineapple = replace(_crop("Pineapple"), crop_type="annual")
    monkeypatch.setattr(planner_service, "score", lambda crop, context: _result(crop))

    timeline, _ = planner_service._build_annual_sequence((pineapple,), _context)

    assert [item.month for item in timeline] == list(range(1, 13))
    assert all(1 <= item.month <= 12 for item in timeline)
    assert all(item.continues_next_year for item in timeline)
    assert not any(item.stage == "harvest" for item in timeline)
