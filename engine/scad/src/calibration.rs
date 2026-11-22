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
