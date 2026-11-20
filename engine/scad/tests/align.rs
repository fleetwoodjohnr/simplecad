//! Align and Combine rebuild as the Python app builds them: a project made by
//! `scripts/engine_oracles.py align_project()` with every Align mode (anchors,
//! edge distances against a saved frame, the pre-anchor Center, Concentric) and
//! every Combine (join, cut keeping its tool, intersect with two tools), rebuilt
//! here from its recipe alone, must give Python's bodies and step messages.

use scad::document::Model;
use scad::rebuild::rebuild;
use serde_json::Value;
use std::collections::{BTreeSet, HashMap};

#[test]
fn every_align_and_combine_rebuilds_from_the_recipe_as_python_built_it() {
    let original = std::fs::read("tests/data/align.scad3").unwrap();
    let expected: Value =
        serde_json::from_str(&std::fs::read_to_string("tests/data/align.json").unwrap()).unwrap();
    let project = scad::project::read(&original).unwrap();
    let mut model = Model::open(&project);
    let all: BTreeSet<String> = model.payload["features"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| f["id"].as_str().unwrap().to_string())
        .collect();
    let report = rebuild(&mut model, &original, &HashMap::new(), &all).unwrap();
    assert!(report.failures.is_empty(), "{:?}", report.failures);
    assert_eq!(report.rebuilt.len(), all.len());

    let bodies = expected["bodies"].as_object().unwrap();
    assert_eq!(report.bodies.len(), bodies.len());
    for body in &report.bodies {
        let want = &bodies[&body.name];
        let shape = body.shape.as_ref().unwrap();
        let volume = occt::volume(shape);
        assert!(
            (volume - want["volume"].as_f64().unwrap()).abs() < 1e-6,
            "{} volume {volume}",
            body.name
        );
        let found = occt::bounds(shape).unwrap();
        for (a, b) in found.iter().zip(want["box"].as_array().unwrap()) {
            assert!(
                (a - b.as_f64().unwrap()).abs() < 1e-6,
                "{} box {found:?} {want}",
                body.name
            );
        }
    }
    for feature in model.payload["features"].as_array().unwrap() {
        let name = feature["name"].as_str().unwrap();
        let state = &model.payload["diagnostics"][feature["id"].as_str().unwrap()];
        let want = &expected["messages"][name];
        assert_eq!(state["state"], want[0], "{name}");
        assert_eq!(state["message"], want[1], "{name}");
    }
}
