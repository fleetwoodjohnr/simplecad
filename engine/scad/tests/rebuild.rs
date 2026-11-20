//! A model rebuilds as the Python app's `Rebuilder` would: saved results are
//! taken while their signatures match, an edited step and everything after it
//! run again, and a face reference finds its face in the new shape.

use scad::document::Model;
use scad::rebuild::{Shape, rebuild};
use serde_json::json;
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

fn top(shape: &occt::Shape) -> (u32, serde_json::Value) {
    let count = occt::faces(shape).unwrap().len() as u32;
    let (i, f) = (0..count)
        .map(|i| (i, occt::face_fingerprint(shape, i).unwrap()))
        .max_by(|a, b| a.1.center[2].total_cmp(&b.1.center[2]))
        .unwrap();
    let print = json!({ "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
        "direction": f.direction, "radius": null, "neighbours": [] });
    (i, print)
}

#[test]
fn an_edited_pull_rebuilds_with_the_pulls_after_it() {
    let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
    let mut current = shape;
    for distance in [5.0, 3.0] {
        let (face, print) = top(&current);
        let reference = model.face_ref("Box", face, print);
        current = occt::push_pull(&current, face, distance).unwrap();
        model
            .push_pull(
                "Box",
                reference,
                &distance.to_string(),
                occt::write_brep(&current).unwrap(),
            )
            .unwrap();
    }
    assert_eq!(occt::volume(&current), 40.0 * 30.0 * 28.0);
    let id = |m: &Model, i: usize| m.payload["features"][i]["id"].as_str().unwrap().to_string();

    // Everything saved: nothing runs.
    let shown: HashMap<String, Shape> = HashMap::from([(
        model.payload["bodies"][0]["id"].as_str().unwrap().into(),
        Rc::new(current),
    )]);
    let report = rebuild(&mut model, &[], &shown, &BTreeSet::new()).unwrap();
    assert!(report.rebuilt.is_empty() && report.failures.is_empty());
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 28.0
    );

    // From the recipe: every step runs, the pulls finding their faces again.
    let all: BTreeSet<String> = (0..3).map(|i| id(&model, i)).collect();
    let report = rebuild(&mut model, &[], &HashMap::new(), &all).unwrap();
    assert_eq!(report.rebuilt.len(), 3, "{:?}", report.failures);
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 28.0
    );

    // The first pull made 8 mm: it and the pull after it run, on the taller box.
    model.payload["features"][1]["inputs"]["distance"] = json!("8 mm");
    let edited = BTreeSet::from([id(&model, 1), id(&model, 2)]);
    let report = rebuild(&mut model, &[], &HashMap::new(), &edited).unwrap();
    assert_eq!(report.rebuilt, [id(&model, 1), id(&model, 2)]);
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 31.0
    );
    let geometry = &model.payload["geometry"][model.payload["bodies"][0]["id"].as_str().unwrap()];
    assert_eq!(geometry["current"], json!(true));
    assert_eq!(
        model.payload["diagnostics"][id(&model, 2)]["state"],
        json!("ok")
    );

    // A step that has to run and is not ported yet fails in words, keeping the body.
    model.payload["features"][2]["type"] = json!("loft");
    let last = BTreeSet::from([id(&model, 2)]);
    let shown: HashMap<String, Shape> = report
        .bodies
        .iter()
        .map(|b| (b.id.clone(), b.shape.clone().unwrap()))
        .collect();
    let report = rebuild(&mut model, &[], &shown, &last).unwrap();
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 31.0
    );
    assert!(report.failures[&id(&model, 2)].contains("Edit it in SimpleCAD 0.4"));
    assert_eq!(
        model.payload["diagnostics"][id(&model, 2)]["state"],
        json!("failed")
    );
    assert_eq!(
        model.payload["geometry"][model.payload["bodies"][0]["id"].as_str().unwrap()]["current"],
        json!(false)
    );
}

#[test]
fn a_saved_scale_rebuilds_each_axis_about_the_saved_pivot() {
    let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let bounds = occt::bounds(&shape).unwrap();
    let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
    let producer = model.payload["features"][0]["id"].clone();
    let body = model.payload["bodies"][0]["id"].clone();
    model.payload["features"].as_array_mut().unwrap().push(json!({
        "id": "scale", "type": "scale", "name": "Scale",
        "inputs": { "body": { "__body__": "Box" }, "factor": "2", "sy": "1 / 2", "sz": "3", "pivot": [4.0, 5.0, 6.0] },
        "outputs": ["Box"], "output_ids": { "Box": body },
        "bindings": { "Box": { "feature_id": producer, "object_id": body } },
        "suppressed": false, "dropped": [],
    }));
    let result = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(result.failures.is_empty(), "{:?}", result.failures);
    let scaled = result.bodies[0].shape.as_ref().unwrap();
    assert!((occt::volume(scaled) - 72_000.0).abs() < 1e-6);
    let after = occt::bounds(scaled).unwrap();
    for (axis, (pivot, factor)) in [(4.0, 2.0), (5.0, 0.5), (6.0, 3.0)].into_iter().enumerate() {
        for i in [axis, axis + 3] {
            let expected = pivot + (bounds[i] - pivot) * factor;
            assert!((after[i] - expected).abs() < 1e-6);
        }
    }
}

#[test]
fn saved_profile_primitives_match_the_python_defaults() {
    for (kind, volume) in [
        ("polygon_prism", 11_691.342_951_089_92),
        ("oval", 7853.981633974482),
        ("pill", 5517.145867644258),
        ("star", 3289.811924358176),
        ("heart", 4965.975557280058),
        ("cross", 5000.0),
        ("crescent", 2400.143933034302),
        ("lightning", 1668.4875),
    ] {
        let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
        let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
        let feature = &mut model.payload["features"][0];
        feature["type"] = json!(kind);
        feature["inputs"] = json!({});
        let result = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
        assert!(result.failures.is_empty(), "{kind}: {:?}", result.failures);
        let shape = result.bodies[0].shape.as_ref().unwrap();
        assert!((occt::volume(shape) - volume).abs() < 1e-6, "{kind}");
    }
}

#[test]
fn legacy_edge_finishes_follow_a_reference_with_an_outdated_index() {
    let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let edges = occt::edge_fingerprints(&shape).unwrap();
    let (index, edge) = edges
        .iter()
        .enumerate()
        .find(|(_, e)| (e.curve.measure - 20.0).abs() < 1e-9)
        .unwrap();
    let f = &edge.curve;
    for (kind, size_key, reduction) in [
        ("fillet", "radius", (4.0 - std::f64::consts::PI) * 20.0),
        ("chamfer", "distance", 40.0),
    ] {
        let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
        let producer = model.payload["features"][0]["id"].clone();
        let body = model.payload["bodies"][0]["id"].clone();
        let reference = json!({ "__ref__": { "feature_id": producer, "kind": "edge", "body": "Box",
            "index_hint": (index + 1) % edges.len(), "fingerprint": {
                "kind": "edge", "geometry": f.geometry, "measure": f.measure, "center": f.center,
                "direction": f.direction, "radius": null, "neighbours": [],
            },
        }});
        model.payload["features"].as_array_mut().unwrap().push(json!({
            "id": "finish", "type": kind, "name": "Finish", "outputs": ["Box"], "output_ids": { "Box": body },
            "inputs": { "body": { "__body__": "Box" }, "edges": [reference], size_key: "2" },
            "bindings": { "Box": { "feature_id": producer, "object_id": body } },
            "suppressed": false, "dropped": [],
        }));
        let report = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
        assert!(report.failures.is_empty(), "{kind}: {:?}", report.failures);
        let finished = report.bodies[0].shape.as_ref().unwrap();
        assert!(
            (occt::volume(finished) - (24_000.0 - reduction)).abs() < 1e-6,
            "{kind}"
        );
    }
}

#[test]
fn a_saved_split_consumes_its_source_and_keeps_both_output_ids() {
    let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
    let producer = model.payload["features"][0]["id"].clone();
    let body = model.payload["bodies"][0]["id"].clone();
    model.payload["features"].as_array_mut().unwrap().push(json!({
        "id": "split", "type": "split", "name": "Split", "outputs": ["Near", "Far"],
        "output_ids": {"Near": "near", "Far": "far"},
        "inputs": { "body": { "__body__": "Box" }, "position": "10 mm", "names": ["Old A", "Old B"] },
        "bindings": { "Box": { "feature_id": producer, "object_id": body } },
        "suppressed": false, "dropped": [],
    }));
    let report = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(report.failures.is_empty(), "{:?}", report.failures);
    assert_eq!(report.bodies.len(), 2);
    for (id, name, volume) in [("near", "Near", 6_000.0), ("far", "Far", 18_000.0)] {
        let body = report.bodies.iter().find(|body| body.id == id).unwrap();
        assert_eq!(body.name, name);
        assert!((occt::volume(body.shape.as_ref().unwrap()) - volume).abs() < 1e-6);
    }
}
