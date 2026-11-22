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

#[test]
fn the_test_print_is_a_pin_strip_and_a_labelled_plate_that_import_under_their_names() {
    use std::f64::consts::PI;
    let pieces = scad::calibration::test_print().unwrap();
    let names: Vec<_> = pieces.iter().map(|(name, _)| name.as_str()).collect();
    assert_eq!(names, ["Calibration pins", "Calibration plate"]);
    let (pins, plate) = (&pieces[0].1, &pieces[1].1);
    // Eight nominal 10 mm pins, 8 mm tall, on a 144 x 18 x 5 mm strip.
    let strip = 144.0 * 18.0 * 5.0 + 8.0 * PI * 25.0 * 8.0;
    assert!(
        (occt::volume(pins) - strip).abs() < 1e-6,
        "{}",
        occt::volume(pins)
    );
    // A 144 x 24 x 5 mm plate less each hole, 10 mm plus its clearance across, plus the raised labels.
    let holes: f64 = scad::calibration::SWEEP
        .iter()
        .map(|c| PI * ((10.0 + c) / 2.0).powi(2) * 5.0)
        .sum();
    let open = 144.0 * 24.0 * 5.0 - holes;
    let labels = occt::volume(plate) - open;
    assert!((0.0..200.0).contains(&labels), "{labels}");
    assert!(occt::is_valid(plate).unwrap() && occt::is_valid(pins).unwrap());

    let mut shapes = occt::shapes_new();
    for (_, shape) in &pieces {
        occt::shapes_push(shapes.pin_mut(), shape);
    }
    let step = occt::write_named_step(
        &shapes,
        &names
            .iter()
            .map(|name| name.to_string())
            .collect::<Vec<_>>(),
    )
    .unwrap();
    let imported: Vec<_> = occt::read_step(&step, "Calibration")
        .unwrap()
        .into_iter()
        .map(|body| body.name)
        .collect();
    assert_eq!(imported, names);
}
