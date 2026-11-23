//! Joints built and saved by SimpleCAD 0.4.4, rebuilt from the recipe alone: every body is what 0.4.4 saved.

use std::collections::{BTreeSet, HashMap};

/// Hinges: a box and its lid, joined along the box's top back edge, as 0.4.4's tests/test_hinge.py builds them.
#[test]
fn hinges_rebuild_from_their_recipes_as_simplecad_044_built_them() {
    rebuilds_as_saved("hinge", &["knuckle", "pin", "snap", "stop7", "living"]);
}

/// Snap tabs and tab & slot keys between two stacked boxes, saved as 0.4.4's Joint tool saves them.
#[test]
fn snap_tabs_and_keys_rebuild_from_their_recipes_as_simplecad_044_built_them() {
    rebuilds_as_saved(
        "clip",
        &["snap_tab", "snap_tab_b", "tab_slot", "tab_slot_large"],
    );
}

fn rebuilds_as_saved(folder: &str, kinds: &[&str]) {
    let folder = format!("{}/tests/data/{folder}", env!("CARGO_MANIFEST_DIR"));
    for kind in kinds {
        let original = std::fs::read(format!("{folder}/{kind}.scad3")).unwrap();
        let project = scad::project::read(&original).unwrap();
        let mut model = scad::document::Model::open(&project);
        let stale: BTreeSet<String> = model.payload["features"]
            .as_array()
            .unwrap()
            .iter()
            .map(|feature| feature["id"].as_str().unwrap().to_string())
            .collect();
        let report =
            scad::rebuild::rebuild(&mut model, &original, &HashMap::new(), &stale).unwrap();
        assert!(report.failures.is_empty(), "{kind}: {:?}", report.failures);
        let saved: HashMap<_, _> = project
            .bodies
            .iter()
            .map(|body| (body.name.clone(), body))
            .collect();
        assert_eq!(report.bodies.len(), saved.len(), "{kind}");
        for body in &report.bodies {
            let built = body.shape.as_ref().unwrap();
            let mut python = occt::read_brep(saved[&body.name].brep.as_ref().unwrap()).unwrap();
            if let Some(matrix) = &saved[&body.name].location {
                python = occt::located(&python, matrix).unwrap();
            }
            let (ours, theirs) = (occt::volume(built), occt::volume(&python));
            assert!(
                (ours - theirs).abs() < 1e-6 * theirs.abs().max(1.0),
                "{kind} {}: {ours} vs {theirs}",
                body.name
            );
            let (a, b) = (occt::bounds(built).unwrap(), occt::bounds(&python).unwrap());
            assert!(
                a.iter().zip(&b).all(|(x, y)| (x - y).abs() < 1e-6),
                "{kind} {}: {a:?} vs {b:?}",
                body.name
            );
            assert_eq!(
                occt::face_occurrences(built),
                occt::face_occurrences(&python),
                "{kind} {}",
                body.name
            );
        }
    }
}

/// A sliding style this version can't build yet fails by name, and its parts keep the last good shapes.
#[test]
fn an_unported_clip_style_says_so_and_keeps_the_saved_parts() {
    let folder = format!("{}/tests/data/clip", env!("CARGO_MANIFEST_DIR"));
    let original = std::fs::read(format!("{folder}/tab_slot.scad3")).unwrap();
    let project = scad::project::read(&original).unwrap();
    let mut model = scad::document::Model::open(&project);
    let features = model.payload["features"].as_array_mut().unwrap();
    features.last_mut().unwrap()["inputs"]["style"] = "dovetail".into();
    let stale: BTreeSet<String> = features
        .iter()
        .map(|feature| feature["id"].as_str().unwrap().to_string())
        .collect();
    let report = scad::rebuild::rebuild(&mut model, &original, &HashMap::new(), &stale).unwrap();
    let errors: Vec<_> = report.failures.values().collect();
    assert!(
        errors.len() == 1 && errors[0].contains("cannot build it yet"),
        "{errors:?}"
    );
    let names: BTreeSet<_> = report
        .bodies
        .iter()
        .filter(|body| body.shape.is_some())
        .map(|body| body.name.as_str())
        .collect();
    assert_eq!(names, BTreeSet::from(["Bottom", "Top"]));
}
