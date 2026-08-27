"""Fit & Clearance calibration."""

from __future__ import annotations

import json

import pytest

from simplecad.kernel import calibration
from simplecad.kernel.calibration import (
    DEFAULT_SWEEP, build_model, effective_fits, effective_thread_clearance,
    load_measurements, save_measurements,
)
from simplecad.kernel.detect import cylindrical_faces
from simplecad.kernel.occ import bounding_box, is_valid
from simplecad.kernel.thread_specs import fit_presets, printer_profile


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the user's real printer profiles."""
    monkeypatch.setattr(calibration, "USER_PROFILE_DIR", str(tmp_path / "printers"))
    yield


@pytest.fixture(scope="module")
def model():
    return build_model()


def test_the_model_is_two_printable_pieces(model):
    assert is_valid(model.pins)
    assert is_valid(model.plate)


def test_the_holes_carry_the_clearance_and_the_pins_stay_nominal(model):
    """Clearance goes on the female side, exactly as everywhere else."""
    holes = sorted(
        round(info.diameter, 2)
        for _face, info in cylindrical_faces(model.plate) if info.internal
    )
    assert holes == [round(model.pin_diameter + c, 2) for c in DEFAULT_SWEEP]

    pins = {
        round(info.diameter, 2)
        for _face, info in cylindrical_faces(model.pins) if not info.internal
    }
    assert pins == {round(model.pin_diameter, 2)}, "every pin is at nominal"


def test_the_model_fits_a_normal_build_plate(model):
    profile = printer_profile()
    for shape in (model.pins, model.plate):
        low, high = bounding_box(shape)
        assert (high[0] - low[0]) < profile["build_volume"]["x"]
        assert (high[1] - low[1]) < profile["build_volume"]["y"]


def test_it_describes_itself_in_useful_terms(model):
    text = model.describe()
    assert "8 test fits" in text
    assert "0.05" in text and "0.50" in text
    assert "10 mm pin" in text


def test_an_empty_sweep_is_refused():
    from simplecad.core.errors import CadError

    with pytest.raises(CadError):
        build_model(clearances=())


# ----------------------------------------------------------------------
def test_shipped_defaults_apply_before_calibration():
    values = effective_fits("some_printer")
    assert values == {
        key: entry["clearance"] for key, entry in fit_presets().items()
    }


def test_measured_values_override_the_defaults():
    save_measurements(
        "my_printer", printer_profile(),
        {"snug": 0.12, "press": -0.02}, thread_clearance=0.17,
    )
    values = effective_fits("my_printer")
    assert values["snug"] == pytest.approx(0.12)
    assert values["press"] == pytest.approx(-0.02)
    # Untouched classes keep their shipped value.
    assert values["loose"] == pytest.approx(fit_presets()["loose"]["clearance"])


def test_a_measured_thread_clearance_is_used():
    assert effective_thread_clearance("uncalibrated") == pytest.approx(0.20)
    save_measurements("tuned", printer_profile(), {}, thread_clearance=0.13)
    assert effective_thread_clearance("tuned") == pytest.approx(0.13)


def test_the_saved_profile_is_readable_json():
    path = save_measurements(
        "readable", printer_profile(), {"snug": 0.11}, thread_clearance=0.19
    )
    with open(path) as handle:
        data = json.load(handle)
    assert data["calibrated"] is True
    assert data["measured_fits"]["snug"] == pytest.approx(0.11)
    assert data["name"] == printer_profile()["name"]


def test_loading_an_uncalibrated_printer_returns_nothing():
    assert load_measurements("never_seen") is None
