//! Delete, as SimpleCAD 0.4.4 deletes: on a 0.4.4 project of a plate and four boxes laid out by one Arrange step.

use scad::document::Model;
use std::collections::{BTreeSet, HashMap};

fn grid() -> (Vec<u8>, Model) {
    let original = std::fs::read(format!(
        "{}/tests/data/arrange/grid.scad3",
        env!("CARGO_MANIFEST_DIR")
    ))
    .unwrap();
    let model = Model::open(&scad::project::read(&original).unwrap());
    (original, model)
}

fn names(report: &scad::rebuild::Report) -> BTreeSet<&str> {
    report
        .bodies
        .iter()
        .map(|body| body.name.as_str())
        .collect()
}

fn step<'a>(model: &'a Model, kind: &str) -> &'a serde_json::Value {
    model.payload["features"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["type"] == kind)
        .unwrap()
}

/// As in 0.4.4 (its `_retire_body` run on this project gives the same), the arrangement also read A, so it now needs
/// its input repaired, and keeps showing its last good bodies until then.
#[test]
fn a_deleted_body_takes_its_own_steps_and_a_step_writing_others_stops_writing_it() {
    let (original, mut model) = grid();
    let made_a = model.payload["features"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["type"] == "box" && f["outputs"] == serde_json::json!(["A"]))
        .unwrap()["id"]
        .clone();
    let report = model
        .delete_bodies(&["A".into()], None, &original, &HashMap::new())
        .unwrap();
    let arrange = step(&model, "arrange")["id"].as_str().unwrap().to_string();
    assert_eq!(
        report.failures[&arrange],
        "The object 'A' this feature needs was deleted. Undo the deletion or re-select its input."
    );
    assert_eq!(names(&report), BTreeSet::from(["A", "Base", "B", "C", "D"]));
    // The box that made A goes and its id is retired; the arrangement writes only the rest.
    assert!(
        model.payload["features"]
            .as_array()
            .unwrap()
            .iter()
            .all(|f| f["id"] != made_a)
    );
    assert!(
        model.payload["retired_ids"]
            .as_array()
            .unwrap()
            .contains(&made_a)
    );
    assert_eq!(step(&model, "arrange")["dropped"], serde_json::json!(["A"]));
    assert_eq!(
        model.history()["undo"].as_array().unwrap().last().unwrap()["label"],
        "Delete"
    );

    // Undo brings A and the step that made it back.
    model.move_history(false).unwrap();
    assert!(
        model.payload["features"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f["id"] == made_a)
    );
    assert_eq!(step(&model, "arrange")["dropped"], serde_json::json!([]));
}

#[test]
fn a_step_reading_a_deleted_body_keeps_its_bodies_and_asks_for_repair() {
    let (original, mut model) = grid();
    let report = model
        .delete_bodies(&["Base".into()], None, &original, &HashMap::new())
        .unwrap();
    let arrange = step(&model, "arrange")["id"].as_str().unwrap().to_string();
    assert!(
        report.failures[&arrange].contains("was deleted"),
        "{:?}",
        report.failures
    );
    assert_eq!(names(&report), BTreeSet::from(["A", "B", "C", "D"]));
}
