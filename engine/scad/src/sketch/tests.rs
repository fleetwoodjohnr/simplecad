use super::*;
use serde_json::json;

#[test]
fn adjusts_saved_geometry_and_preserves_dimension_expressions() {
    let recipe = json!({
        "points": [{"id": "a", "x": 0, "y": 0, "fixed": true},
                   {"id": "b", "x": 8, "y": 3}],
        "entities": [{"id": "ab", "kind": "line", "start": "a", "end": "b"}],
        "constraints": [
            {"id": "pin", "kind": "fix", "refs": ["a"]},
            {"id": "level", "kind": "horizontal", "refs": ["ab"]},
            {"id": "width", "kind": "distance", "refs": ["a", "b"], "value": "width / 2"},
            {"id": "reference", "kind": "distance", "refs": ["a", "b"], "value": 999, "driving": false}
        ]
    });
    let out = solve(&recipe, &HashMap::from([("width".into(), 24.0)])).unwrap();
    assert_eq!(out.state, State::Fully);
    assert_eq!(out.dof, 0);
    assert!(out.residual < 1e-8);
    assert!((out.recipe["points"][1]["x"].as_f64().unwrap() - 12.0).abs() < 1e-8);
    assert!(out.recipe["points"][1]["y"].as_f64().unwrap().abs() < 1e-8);
    assert_eq!(out.recipe["constraints"][2]["value"], "width / 2");
}

#[test]
fn distinguishes_redundant_and_conflicting_dimensions() {
    let mut recipe = json!({
        "points": [{"id": "p", "x": 0, "y": 0}],
        "entities": [{"id": "c", "kind": "circle", "center": "p", "radius": 4}],
        "constraints": [
            {"id": "radius", "kind": "radius", "refs": ["c"], "value": 5},
            {"id": "dia", "kind": "diameter", "refs": ["c"], "value": 10}
        ]
    });
    let out = solve(&recipe, &HashMap::new()).unwrap();
    assert_eq!(out.state, State::Redundant);
    assert_eq!(out.dof, 2);
    assert!((out.recipe["entities"][0]["radius"].as_f64().unwrap() - 5.0).abs() < 1e-8);

    recipe["constraints"][1]["value"] = json!(16);
    let out = solve(&recipe, &HashMap::new()).unwrap();
    assert_eq!(out.state, State::Conflicting);
    assert!(out.residual > 1.0);
    assert!(out.conflicting.contains(&"radius".into()));
    assert!(out.conflicting.contains(&"dia".into()));
    assert_eq!(out.recipe, recipe);
}

#[test]
fn rejects_missing_references_without_corrupting_the_recipe() {
    let recipe = json!({
        "points": [{"id": "a", "x": 3, "y": 4}],
        "entities": [{"id": "ab", "kind": "line", "start": "a", "end": "missing"}]
    });
    assert!(
        solve(&recipe, &HashMap::new())
            .unwrap_err()
            .message
            .contains("missing")
    );
}

#[test]
fn replays_a_saved_sketch_and_solids_without_a_geometry_cache() {
    use crate::document::Model;
    use crate::project;
    use crate::rebuild::rebuild;
    use std::collections::BTreeSet;
    use std::f64::consts::PI;

    let mut payload = json!({
        "format": 4, "title": "Sketch profiles",
        "parameters": [{"name": "width", "expression": "12 mm", "dimension": "length"}],
        "features": [
            {"id": "plate", "name": "Profile", "type": "sketch", "inputs": {"sketch": {
                "name": "Profile",
                "plane": {"origin": [0, 0, 2], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
                "points": [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 40, "y": 0},
                    {"id": "c", "x": 40, "y": 30}, {"id": "d", "x": 0, "y": 30},
                    {"id": "h", "x": 15, "y": 10}, {"id": "j", "x": 25, "y": 20}],
                "entities": [{"kind": "line", "id": "ab", "start": "a", "end": "b"},
                    {"kind": "line", "id": "bc", "start": "b", "end": "c"},
                    {"kind": "line", "id": "cd", "start": "c", "end": "d"},
                    {"kind": "line", "id": "da", "start": "d", "end": "a"},
                    {"kind": "circle", "id": "hole", "center": "h", "radius": 4},
                    {"kind": "ellipse", "id": "ellipse", "center": "j", "major": 3, "minor": 2, "rotation": 0.4},
                    {"kind": "line", "id": "guide", "start": "a", "end": "h", "construction": true}],
                "constraints": [{"kind": "radius", "id": "dimension", "refs": ["hole"],
                    "value": 4, "expression": "width / 3"}]
            }}},
            {"id": "extrude", "name": "Plate", "type": "extrude", "inputs": {
                "sketch": "Profile", "distance": "10 mm", "symmetric": true
            }},
            {"id": "round", "name": "Round", "type": "sketch", "inputs": {"sketch": {
                "name": "Round",
                "points": [{"id": "p", "x": 0, "y": 12}],
                "entities": [{"kind": "circle", "id": "c", "center": "p", "radius": 2}]
            }}},
            {"id": "revolve", "name": "Ring", "type": "revolve", "inputs": {
                "sketch": "Round", "angle": "360 deg"
            }}
        ]
    });
    crate::graph::normalise(&mut payload);
    let mut model = Model::new(payload);
    let report = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(report.failures.is_empty(), "{:?}", report.failures);
    assert_eq!(report.bodies.len(), 2);
    let plate = report.bodies[0].shape.as_ref().unwrap();
    let ring = report.bodies[1].shape.as_ref().unwrap();
    assert!(occt::is_valid(plate).unwrap());
    assert!(occt::is_valid(ring).unwrap());
    assert!((occt::volume(plate) - (1200. - PI * 22.) * 10.).abs() < 1e-5);
    assert!((occt::volume(ring) - 96. * PI * PI).abs() < 1e-5);
    let bounds = occt::bounds(plate).unwrap();
    assert!((bounds[2] + 3.).abs() < 1e-6 && (bounds[5] - 7.).abs() < 1e-6);
    assert!(
        model.payload["cache"]["entries"]
            .as_object()
            .unwrap()
            .values()
            .any(|entry| {
                entry["feature_id"] == "plate"
                    && entry["outputs"]
                        .as_object()
                        .unwrap()
                        .values()
                        .any(|output| output["kind"] == "sketch")
            })
    );

    let original = model.save(&[]).unwrap();
    let project = project::read(&original).unwrap();
    let mut restored = Model::open(&project);
    restored.payload["parameters"][0]["expression"] = json!("15 mm");
    let report = rebuild(&mut restored, &original, &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(report.failures.is_empty(), "{:?}", report.failures);
    assert_eq!(report.rebuilt, vec!["plate", "extrude"]);
    let plate = report.bodies[0].shape.as_ref().unwrap();
    assert!((occt::volume(plate) - (1200. - PI * 31.) * 10.).abs() < 1e-5);
}
