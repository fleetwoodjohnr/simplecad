//! SimpleCAD 0.4's printer calibration (`kernel/calibration.py`): fits measured on the user's printer
//! replace the shipped presets wherever a named fit is resolved, as 0.4's rebuild does.

use serde_json::Value;
use std::sync::{OnceLock, RwLock};

const FITS: &str = include_str!("../../../simplecad/data/fits.json");

/// The saved user profile, `printers/<id>.json` in 0.4's data folder, if the printer was calibrated.
static MEASURED: RwLock<Option<Value>> = RwLock::new(None);

fn shipped() -> &'static Value {
    static TABLE: OnceLock<Value> = OnceLock::new();
    TABLE.get_or_init(|| serde_json::from_str(FITS).expect("embedded fits table"))
}

/// Use a saved profile's measurements from now on; `None` (or a profile without any) goes back to the shipped ones.
pub fn set(profile: Option<&str>) -> Result<(), String> {
    let value = profile
        .map(serde_json::from_str::<Value>)
        .transpose()
        .map_err(|_| "The printer calibration file is damaged.".to_string())?
        .filter(Value::is_object);
    *MEASURED
        .write()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = value;
    Ok(())
}

fn measured(key: &str) -> Option<Value> {
    MEASURED
        .read()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .as_ref()?
        .get(key)
        .cloned()
}

/// `effective_fits`: a fit preset's clearance (press, snug, sliding, loose, thread), measured where available.
pub fn fit(name: &str) -> Option<f64> {
    measured("measured_fits")
        .and_then(|fits| fits.get(name)?.as_f64())
        .or_else(|| shipped()["fits"][name]["clearance"].as_f64())
}

/// `effective_thread_clearance`: a thread-clearance preset's diametral allowance. Calibration records the
/// Normal fit; the other presets keep their shipped spacing from it, never closer than 0.1 mm.
pub fn thread_clearance(name: &str) -> Option<f64> {
    let table = &shipped()["thread_clearance"];
    let preset = table[name]["clearance"].as_f64()?;
    Some(
        match measured("measured_thread_clearance").and_then(|value| value.as_f64()) {
            Some(normal) => (normal + preset - table["normal"]["clearance"].as_f64()?).max(0.10),
            None => preset,
        },
    )
}

/// `calibration.build_model`'s clearances, in mm, and its pin, plate and spacing sizes.
pub const SWEEP: [f64; 8] = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50];
const PIN: f64 = 10.0;
const PIN_HEIGHT: f64 = 8.0;
const PLATE: f64 = 5.0;
const PITCH: f64 = 18.0;
/// The band along the plate's front that carries the labels, clear of the holes.
const LABELS: f64 = 6.0;

/// SimpleCAD 0.4's calibration test print, as named pieces: a strip of nominal pins, and a plate whose
/// holes are opened by each clearance and labelled with it. Print both; the first hole that fits the way
/// you want is that fit's clearance.
pub fn test_print() -> Result<Vec<(String, occt::UniquePtr<occt::Shape>)>, String> {
    let kernel = |error: occt::Exception| error.what().to_string();
    let width = PITCH * SWEEP.len() as f64;
    let cylinder = |radius: f64, height: f64, at: [f64; 3]| {
        occt::place(
            &*occt::make_cylinder(radius, height).map_err(kernel)?,
            &[at[0], at[1], at[2], 0.0, 0.0, 0.0],
        )
        .map_err(kernel)
    };
    let mut pins = occt::shapes_new();
    let mut holes = occt::shapes_new();
    for (index, clearance) in SWEEP.iter().enumerate() {
        let x = PITCH * (index as f64 + 0.5);
        occt::shapes_push(
            pins.pin_mut(),
            &*cylinder(PIN / 2.0, PIN_HEIGHT, [x, PITCH / 2.0, PLATE])?,
        );
        occt::shapes_push(
            holes.pin_mut(),
            &*cylinder(
                (PIN + clearance) / 2.0,
                PLATE + 2.0,
                [x, LABELS + PITCH / 2.0, -1.0],
            )?,
        );
    }
    let strip = occt::make_box(width, PITCH, PLATE).map_err(kernel)?;
    let pins =
        occt::unify(&*occt::combine(0, &strip, &pins, 0.0).map_err(kernel)?).map_err(kernel)?;
    let blank = occt::make_box(width, PITCH + LABELS, PLATE).map_err(kernel)?;
    let mut plate =
        occt::unify(&*occt::combine(1, &blank, &holes, 0.0).map_err(kernel)?).map_err(kernel)?;
    // The top face's frame, taken once: each label's offset is from the blank's centre, not the face that the
    // labels before it changed.
    let top = occt::faces(&plate)
        .map_err(kernel)?
        .iter()
        .position(|face| face.normal[2] > 0.99 && (face.center[2] - PLATE).abs() < 1e-6)
        .ok_or("The calibration plate has no top face.")?;
    let frame =
        occt::plane_frame(&*occt::sub_face(&plate, top as u32).map_err(kernel)?).map_err(kernel)?;
    let centre = [width / 2.0, (PITCH + LABELS) / 2.0];
    let along = |axis: [f64; 3], offset: [f64; 2]| axis[0] * offset[0] + axis[1] * offset[1];
    for (index, clearance) in SWEEP.iter().enumerate() {
        let world = [
            PITCH * (index as f64 + 0.5) - centre[0],
            LABELS / 2.0 - centre[1],
        ];
        let offset = [along(frame.x_dir, world), along(frame.y_dir, world)];
        // A label the fonts cannot draw is a cosmetic loss, as in 0.4: the plate still prints.
        if let Ok(marked) =
            crate::text::raised_label(&plate, &frame, &format!("{clearance:.2}"), 4.0, 0.6, offset)
        {
            plate = marked;
        }
    }
    Ok(vec![
        ("Calibration pins".into(), pins),
        ("Calibration plate".into(), plate),
    ])
}
