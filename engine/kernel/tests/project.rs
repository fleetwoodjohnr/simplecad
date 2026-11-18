use scad_kernel::project::{MAX_MODEL_BYTES, read_model, write_model};

#[test]
fn preserves_recipe_through_zip_round_trip() {
    let json = r#"{"format":5,"title":"Vented wall","history":{"cursor":2}}"#;
    assert_eq!(read_model(&write_model(json).unwrap()), Ok(json.to_owned()));
}

#[test]
fn reports_corrupt_or_oversize_project() {
    assert!(read_model(b"unreadable archive").is_err());
    assert!(write_model(&" ".repeat(MAX_MODEL_BYTES + 1)).is_err());
}
