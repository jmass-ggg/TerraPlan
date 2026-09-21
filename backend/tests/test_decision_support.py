"""Decision-support engine invariants."""

from app.services.decision_support import CROPS, build_decision_support


def _result(**overrides):
    values = {
        "farm_id": "00000000-0000-0000-0000-000000000001",
        "farm_name": "Test Farm",
        "latitude": -1.29,
        "longitude": 36.82,
        "selected_month": 4,
        "rainfall_change_pct": 0,
        "temperature_change_c": 0,
    }
    values.update(overrides)
    return build_decision_support(**values)


def test_planner_returns_all_months_and_ranked_recommendations():
    result = _result()
    assert len(result["months"]) == 12
    assert len(CROPS) == 12
    for month in result["months"]:
        assert len(month["recommendations"]) == 3
        scores = [item["score"] for item in month["recommendations"]]
        assert scores == sorted(scores, reverse=True)
        assert 0 <= min(scores) <= max(scores) <= 100


def test_zero_change_scenario_has_zero_deltas():
    result = _result()
    assert all(item["delta"] == 0 for item in result["comparison"])


def test_drier_hotter_scenario_recalculates_outputs_without_mutating_baseline():
    baseline = _result()
    scenario = _result(rainfall_change_pct=-25, temperature_change_c=2)
    assert scenario["selected_month"]["expected_rainfall_mm"] < baseline["selected_month"]["expected_rainfall_mm"]
    assert scenario["selected_month"]["expected_temperature_c"] == baseline["selected_month"]["expected_temperature_c"] + 2
    assert any(item["delta"] != 0 for item in scenario["comparison"])
    assert _result() == baseline


def test_flood_exposure_is_unknown_without_terrain_evidence():
    result = _result()
    flood = next(item for item in result["risks"] if item["slug"] == "heavy-rain")
    assert flood["level"] == "Unknown"
    assert "no flood probability" in flood["evidence_note"].lower()
