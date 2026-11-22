//! Its own test binary: the calibration is process-wide, as in SimpleCAD 0.4.

#[test]
fn a_saved_calibration_replaces_the_shipped_fits_and_keeps_the_thread_presets_spacing() {
    use scad::calibration::{fit, set, thread_clearance};
    assert_eq!(
        (
            fit("snug"),
            thread_clearance("normal"),
            thread_clearance("loose")
        ),
        (Some(0.15), Some(0.6), Some(0.8))
    );

    set(Some(
        r#"{"id": "elegoo_centauri_carbon_2", "calibrated": true,
        "measured_fits": {"snug": 0.22}, "measured_thread_clearance": 0.45}"#,
    ))
    .unwrap();
    assert_eq!(fit("snug"), Some(0.22));
    assert_eq!(fit("sliding"), Some(0.3), "an unmeasured fit stays shipped");
    let close = |value: Option<f64>, expected: f64| (value.unwrap() - expected).abs() < 1e-12;
    assert!(close(thread_clearance("normal"), 0.45));
    assert!(
        close(thread_clearance("loose"), 0.65),
        "Loose stays 0.2 mm looser than Normal"
    );
    assert!(close(thread_clearance("tight"), 0.25));
    assert_eq!(thread_clearance("bogus"), None);

    assert!(set(Some("{not json")).is_err());
    set(None).unwrap();
    assert_eq!(fit("snug"), Some(0.15));
}
