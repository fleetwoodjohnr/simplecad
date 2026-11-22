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
    model.payload["features"][2]["type"] = json!("future_feature");
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
fn edge_finishes_follow_a_reference_with_an_outdated_index() {
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
        ("edge_finish", "distance", 60.0),
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
        let inputs = if kind == "edge_finish" {
            json!({ "body": { "__body__": "Box" }, "edge_specs": [{ "edge": reference, "type": "chamfer", "mode": "two_distances", "distance": "2", "distance2": "3", "flip": true }] })
        } else {
            json!({ "body": { "__body__": "Box" }, "edges": [reference], size_key: "2" })
        };
        model.payload["features"].as_array_mut().unwrap().push(json!({
            "id": "finish", "type": kind, "name": "Finish", "outputs": ["Box"], "output_ids": { "Box": body },
            "inputs": inputs,
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

#[test]
fn a_new_hexagon_saves_as_an_editable_polygon_and_undo_removes_the_body() {
    let box_shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&box_shape).unwrap()).unwrap();
    let (name, _) = scad::rebuild::create_primitive(
        &mut model,
        "hexagon",
        json!({"across_flats": "17 mm", "height": "8 mm"}),
    )
    .unwrap();
    assert_eq!(name, "Hexagon");
    let bytes = model.save(&[]).unwrap();
    let reopened = scad::project::read(&bytes).unwrap();
    assert_eq!(reopened.model["features"][1]["type"], "polygon_prism");
    assert_eq!(reopened.model["features"][1]["inputs"]["sides"], 6);
    assert_eq!(reopened.bodies[1].name, "Hexagon");
    model.undo().unwrap();
    let undone = model.save(&[]).unwrap();
    let reopened = scad::project::read(&undone).unwrap();
    assert_eq!(reopened.bodies.len(), 1);
    assert_eq!(
        reopened.model["history"]["redo"].as_array().unwrap().len(),
        1
    );
}

#[test]
fn a_saved_through_hole_uses_the_inward_direction_of_the_face() {
    let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
    let (index, fingerprint) = top(&shape);
    let reference = model.face_ref("Box", index, fingerprint);
    let producer = model.payload["features"][0]["id"].clone();
    let body = model.payload["bodies"][0]["id"].clone();
    model.payload["features"].as_array_mut().unwrap().push(json!({
        "id": "hole", "type": "hole", "name": "Hole", "outputs": ["Box"], "output_ids": { "Box": body },
        "inputs": { "body": { "__body__": "Box" }, "face": { "__ref__": reference }, "diameter": "6 mm" },
        "bindings": { "Box": { "feature_id": producer, "object_id": body } },
        "suppressed": false, "dropped": [],
    }));
    let report = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(report.failures.is_empty(), "{:?}", report.failures);
    let expected = 24_000.0 - std::f64::consts::PI * 9.0 * 20.0;
    assert!((occt::volume(report.bodies[0].shape.as_ref().unwrap()) - expected).abs() < 1e-6);
}

#[test]
fn saved_mirror_and_patterns_apply_their_recipe_to_the_whole_body() {
    let box_shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    for (kind, mut inputs, volume, x_min, x_max) in [
        ("mirror", json!({"keep": false}), 24_000.0, -40.0, 0.0),
        (
            "rectangular_pattern",
            json!({"count_x": "2", "spacing_x": "50 mm"}),
            48_000.0,
            0.0,
            90.0,
        ),
        (
            "circular_pattern",
            json!({"count": "4", "total_angle": "360 deg"}),
            96_000.0,
            -40.0,
            40.0,
        ),
    ] {
        let mut model = Model::new_box(occt::write_brep(&box_shape).unwrap()).unwrap();
        let producer = model.payload["features"][0]["id"].clone();
        let body = model.payload["bodies"][0]["id"].clone();
        inputs["body"] = json!({"__body__": "Box"});
        model.payload["features"].as_array_mut().unwrap().push(json!({
            "id": "pattern", "type": kind, "name": "Pattern", "outputs": ["Box"], "output_ids": { "Box": body },
            "inputs": inputs, "bindings": { "Box": { "feature_id": producer, "object_id": body } },
            "suppressed": false, "dropped": [],
        }));
        let report = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
        assert!(report.failures.is_empty(), "{kind}: {:?}", report.failures);
        let moved = report.bodies[0].shape.as_ref().unwrap();
        assert!((occt::volume(moved) - volume).abs() < 1e-6, "{kind}");
        let bounds = occt::bounds(moved).unwrap();
        assert!(
            (bounds[0] - x_min).abs() < 1e-6 && (bounds[3] - x_max).abs() < 1e-6,
            "{kind}: {bounds:?}"
        );
    }
}

#[test]
fn a_circular_pattern_repeats_a_body_in_place_and_edits_its_count_in_one_undo_step() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let id = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let shown = HashMap::from([(id, Rc::new(solid))]);
    let volume =
        |report: &scad::rebuild::Report| occt::volume(report.bodies[0].shape.as_ref().unwrap());
    let inputs = json!({"count": "3", "total_angle": "360", "axis": [0.0, 0.0, 1.0], "center": [-20.0, 0.0, 0.0]});
    let made = model
        .add_body_operation("circular_pattern", "Box", inputs, &[], &shown)
        .unwrap();
    assert!(
        (volume(&made) - 3000.0).abs() < 1e-6,
        "three separate boxes"
    );
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (step.name.as_str(), step.kind.as_str()),
        ("Circular Pattern", "circular_pattern")
    );
    assert_eq!(step.face_settings.as_ref().unwrap()["count"], "3");

    let edited = model
        .edit_step_settings(
            &step.id,
            [("count".to_owned(), "2".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    assert!((volume(&edited) - 2000.0).abs() < 1e-6);
    let bytes = model.save(&[]).unwrap();
    let reopened = scad::project::read(&bytes).unwrap();
    let saved = &reopened.model["features"][1];
    assert_eq!(saved["inputs"]["count"], "2");
    assert_eq!(saved["inputs"]["axis"], json!([0.0, 0.0, 1.0]));
    assert_eq!(
        saved["bindings"]["Box"]["object_id"],
        saved["output_ids"]["Box"]
    );
    model.undo().unwrap();
    let undone = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    assert_eq!(undone.model["features"][1]["inputs"]["count"], "3");
    assert!(
        model
            .add_body_operation("fillet", "Box", json!({}), &[], &shown)
            .is_err()
    );
}

#[test]
fn a_combine_cuts_a_tool_out_of_the_body_and_consumes_it_unless_kept() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let (tool, cutter) = scad::rebuild::create_primitive(
        &mut model,
        "box",
        json!({"width": "4 mm", "depth": "4 mm", "height": "20 mm", "x": "3 mm", "y": "3 mm", "z": "-5 mm"}),
    )
    .unwrap();
    let id = |name: &str| {
        model.payload["bodies"]
            .as_array()
            .unwrap()
            .iter()
            .find(|b| b["name"] == name)
            .unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned()
    };
    let shown = HashMap::from([(id("Box"), Rc::new(solid)), (id(&tool), Rc::new(cutter))]);
    let inputs = json!({"tools": [tool], "operation": "cut", "keep_tool": false});
    let report = model
        .add_body_operation("boolean", "Box", inputs, &[], &shown)
        .unwrap();
    let names: Vec<_> = report
        .bodies
        .iter()
        .map(|body| body.name.as_str())
        .collect();
    assert_eq!(names, ["Box"], "the tool is consumed");
    assert!((occt::volume(report.bodies[0].shape.as_ref().unwrap()) - 840.0).abs() < 1e-6);
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (step.name.as_str(), step.kind.as_str()),
        ("Combine", "boolean")
    );
    let kept = model
        .edit_step_settings(
            &step.id,
            [("keep_tool".to_owned(), "true".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    assert_eq!(kept.bodies.len(), 2, "a kept tool stays");
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    let combine = saved.model["features"]
        .as_array()
        .unwrap()
        .last()
        .unwrap()
        .clone();
    assert_eq!(combine["inputs"]["tools"][0]["__body__"], json!(tool));
    assert_eq!(combine["inputs"]["keep_tool"], json!(true));
    assert!(
        model
            .add_body_operation("boolean", "Box", json!({"tools": ["Box"]}), &[], &shown)
            .is_err()
    );
}

#[test]
fn a_shell_opens_the_picked_face_and_its_thickness_edits_from_history() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let top = (0..occt::faces(&solid).unwrap().len() as u32)
        .max_by(|a, b| {
            let z = |i| occt::face_fingerprint(&solid, i).unwrap().center[2];
            z(*a).total_cmp(&z(*b))
        })
        .unwrap();
    let picked = scad::align::PickedFace {
        name: "Box",
        shape: &solid,
        index: top,
    };
    let shelled = scad::rebuild::create_face_feature(
        &mut model,
        picked,
        "shell",
        json!({"thickness": "1 mm"}),
        None,
    )
    .unwrap();
    // A 10 mm cube with its top open and 1 mm walls: 1000 - 8 × 8 × 9.
    assert!(
        (occt::volume(&shelled) - 424.0).abs() < 1e-6,
        "{}",
        occt::volume(&shelled)
    );
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (
            step.name.as_str(),
            step.face_settings.as_ref().unwrap()["thickness"].as_str()
        ),
        ("Shell", "1 mm")
    );
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    let shell = &saved.model["features"][1];
    assert_eq!(shell["inputs"]["faces"].as_array().map(Vec::len), Some(1));
    let id = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let shown = HashMap::from([(id, Rc::new(shelled))]);
    let edited = model
        .edit_step_settings(
            &step.id,
            [("thickness".to_owned(), "2 mm".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    // 1000 - 6 × 6 × 8.
    assert!((occt::volume(edited.bodies[0].shape.as_ref().unwrap()) - 712.0).abs() < 1e-6);
}

#[test]
fn a_fillet_rounds_the_picked_edges_finds_them_again_and_edits_its_radius() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let id = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let shown = HashMap::from([(id, Rc::new(solid))]);
    let volume =
        |report: &scad::rebuild::Report| occt::volume(report.bodies[0].shape.as_ref().unwrap());
    // Every edge of the cube, rounded 1 mm: the cube less 12 quarter-round edge strips and 8 corners.
    let all: Vec<u32> = (0..12).collect();
    let rounded = model
        .add_edge_finish("fillet", "Box", &all, "1 mm", &[], &shown)
        .unwrap();
    let (r, pi) = (1.0_f64, std::f64::consts::PI);
    let expected =
        1000.0 - 12.0 * 8.0 * (r * r - pi * r * r / 4.0) - 8.0 * (r.powi(3) - pi * r.powi(3) / 6.0);
    assert!(
        (volume(&rounded) - expected).abs() < 1e-3,
        "{} vs {expected}",
        volume(&rounded)
    );
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (
            step.name.as_str(),
            step.face_settings.as_ref().unwrap()["radius"].as_str()
        ),
        ("Fillet", "1 mm")
    );
    let edited = model
        .edit_step_settings(
            &step.id,
            [("radius".to_owned(), "2 mm".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    assert!(
        volume(&edited) < volume(&rounded),
        "a larger radius removes more"
    );
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    let fillet = &saved.model["features"][1];
    assert_eq!(fillet["inputs"]["edges"].as_array().map(Vec::len), Some(12));
    assert_eq!(fillet["inputs"]["edges"][0]["__ref__"]["kind"], "edge");
    assert!(
        model
            .add_edge_finish("fillet", "Box", &[99], "1 mm", &[], &shown)
            .is_err()
    );
}

#[test]
fn grouping_moves_members_out_of_their_old_group_and_ungrouping_hands_them_up() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    for x in ["20 mm", "40 mm"] {
        scad::rebuild::create_primitive(
            &mut model,
            "box",
            json!({"width": "5 mm", "depth": "5 mm", "height": "5 mm", "x": x}),
        )
        .unwrap();
    }
    let names: Vec<String> = model.payload["bodies"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b["name"].as_str().unwrap().to_owned())
        .collect();
    assert!(model.group(&names[..1]).is_err(), "one body is not a group");
    let inner = model.group(&names[..2]).unwrap();
    let outer = model.group(&[inner.clone(), names[2].clone()]).unwrap();
    assert_eq!(
        model.groups(),
        [
            (outer.clone(), vec![inner.clone(), names[2].clone()]),
            (inner.clone(), names[..2].to_vec())
        ]
        .into_iter()
        .rev()
        .collect::<Vec<_>>()
    );
    model.ungroup(&inner).unwrap();
    assert_eq!(
        model.groups(),
        [(
            outer.clone(),
            vec![names[0].clone(), names[1].clone(), names[2].clone()]
        )]
    );
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    assert_eq!(
        saved.model["groups"][0]["members"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    model.undo().unwrap();
    assert_eq!(model.groups().len(), 2, "Ungroup is one undoable step");
}

/// Spondge Holder's Combine5 was saved as built, but its tool's step is gone (bound to no step). Its
/// first rebuild needs a repair, so History says so as the file opens, and the body refuses changes.
#[test]
fn a_step_bound_to_a_deleted_input_needs_attention_and_its_body_refuses_changes() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let (tool, cutter) = scad::rebuild::create_primitive(
        &mut model,
        "box",
        json!({"width": "4 mm", "depth": "4 mm", "height": "20 mm", "x": "3 mm", "y": "3 mm", "z": "-5 mm"}),
    )
    .unwrap();
    let id = |model: &Model, name: &str| {
        model.payload["bodies"]
            .as_array()
            .unwrap()
            .iter()
            .find(|b| b["name"] == name)
            .unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned()
    };
    let shown = HashMap::from([
        (id(&model, "Box"), Rc::new(solid)),
        (id(&model, &tool), Rc::new(cutter)),
    ]);
    let inputs = json!({"tools": [tool], "operation": "cut", "keep_tool": true});
    model
        .add_body_operation("boolean", "Box", inputs, &[], &shown)
        .unwrap();
    assert!(model.editable("Box").is_ok());

    let combine = model.payload["features"]
        .as_array_mut()
        .unwrap()
        .last_mut()
        .unwrap();
    combine["bindings"][&tool]["feature_id"] = json!("");
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (step.name.as_str(), step.state.as_str()),
        ("Combine", "needs_attention")
    );
    assert!(step.message.unwrap().contains("Repair input"));
    assert_eq!(
        model.editable("Box").unwrap_err(),
        "Repair the step marked in History that made \"Box\" before changing it."
    );
}

#[test]
fn every_construction_tool_is_created_from_its_picks_and_rebuilds() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let (rod, round) = scad::rebuild::create_primitive(
        &mut model,
        "cylinder",
        json!({"radius": "3 mm", "height": "8 mm", "x": "30 mm"}),
    )
    .unwrap();
    let id = |model: &Model, name: &str| {
        model.payload["bodies"]
            .as_array()
            .unwrap()
            .iter()
            .find(|b| b["name"] == name)
            .unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned()
    };
    let face = |shape: &occt::Shape, test: &dyn Fn(&occt::FaceFingerprint) -> bool| {
        (0..occt::faces(shape).unwrap().len() as u32)
            .find(|&i| test(&occt::face_fingerprint(shape, i).unwrap()))
            .unwrap()
    };
    let top = face(&solid, &|f| f.geometry == "plane" && f.center[2] > 9.9);
    let bottom = face(&solid, &|f| f.geometry == "plane" && f.center[2] < 0.1);
    let side = face(&round, &|f| f.geometry == "cylinder");
    let shown = HashMap::from([
        (id(&model, "Box"), Rc::new(solid)),
        (id(&model, &rod), Rc::new(round)),
    ]);
    let at = |name: &str, i: u32| vec![(name.to_owned(), i)];
    let mut make = |kind: &str, faces: &[(String, u32)], edges: &[(String, u32)], inputs| {
        model
            .add_construction(kind, faces, edges, inputs, &[], &shown)
            .unwrap_or_else(|error| panic!("{kind}: {error}"));
    };
    make(
        "midplane",
        &[at("Box", top), at("Box", bottom)].concat(),
        &[],
        json!({}),
    );
    make(
        "plane_at_angle",
        &at("Box", top),
        &at("Box", 0),
        json!({"angle": "30"}),
    );
    make(
        "plane_through_points",
        &[],
        &[],
        json!({"points": [[0, 0, 1], [1, 0, 1], [0, 1, 1]]}),
    );
    make(
        "tangent_plane",
        &at(&rod, side),
        &[],
        json!({"around": "90"}),
    );
    make("construction_axis", &[], &at("Box", 0), json!({}));
    make("construction_axis", &at(&rod, side), &[], json!({}));
    make(
        "construction_point",
        &at("Box", top),
        &[],
        json!({"x": "1 mm", "y": "2 mm", "z": "0"}),
    );
    let steps = model.steps();
    let names: Vec<_> = steps.iter().skip(2).map(|s| s.name.as_str()).collect();
    assert_eq!(
        names,
        [
            "Midplane",
            "Plane at Angle",
            "Plane through Points",
            "Tangent Plane",
            "Axis",
            "Axis2",
            "Point"
        ]
    );
    assert!(steps.iter().all(|s| s.state == "ok"));
    let midplane = steps[2]
        .construction_plane
        .as_ref()
        .expect("a plane the sketcher can use");
    assert!(
        (midplane["origin"][2] - 5.0).abs() < 1e-9,
        "halfway between top and bottom"
    );
    assert!(
        model
            .add_construction("midplane", &at("Box", top), &[], json!({}), &[], &shown)
            .is_err()
    );
}

#[test]
fn a_split_consumes_the_body_into_two_named_halves() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let id = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let shown = HashMap::from([(id, Rc::new(solid))]);
    let inputs = json!({"normal": [0.0, 0.0, 1.0], "origin": [5.0, 5.0, 5.0], "position": "2 mm"});
    let report = model
        .add_body_operation("split", "Box", inputs, &[], &shown)
        .unwrap();
    let halves: Vec<_> = report
        .bodies
        .iter()
        .map(|body| {
            (
                body.name.as_str(),
                occt::volume(body.shape.as_ref().unwrap()).round(),
            )
        })
        .collect();
    assert_eq!(halves, [("Box A", 700.0), ("Box B", 300.0)]);
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    let split = saved.model["features"]
        .as_array()
        .unwrap()
        .last()
        .unwrap()
        .clone();
    assert_eq!(split["outputs"], json!(["Box A", "Box B"]));
    assert_eq!(split["inputs"]["names"], json!(["Box A", "Box B"]));
}

#[test]
fn an_arrangement_spaces_bodies_evenly_along_an_axis_and_edits_its_gap() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let mut shown = HashMap::new();
    shown.insert(
        model.payload["bodies"][0]["id"]
            .as_str()
            .unwrap()
            .to_owned(),
        Rc::new(solid),
    );
    for x in ["30 mm", "100 mm"] {
        let (name, shape) = scad::rebuild::create_primitive(
            &mut model,
            "box",
            json!({"width": "10 mm", "depth": "10 mm", "height": "10 mm", "x": x}),
        )
        .unwrap();
        let id = model.payload["bodies"]
            .as_array()
            .unwrap()
            .iter()
            .find(|b| b["name"] == name)
            .unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned();
        shown.insert(id, Rc::new(shape));
    }
    let names: Vec<String> = model.payload["bodies"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b["name"].as_str().unwrap().to_owned())
        .collect();
    let low_x = |report: &scad::rebuild::Report| -> Vec<f64> {
        let mut lows: Vec<f64> = report
            .bodies
            .iter()
            .map(|b| (occt::bounds(b.shape.as_ref().unwrap()).unwrap()[0] * 1e6).round() / 1e6)
            .collect();
        lows.sort_by(f64::total_cmp);
        lows
    };
    let fixed = json!({"mode": "fixed_gap", "axis": "x", "gap": "5 mm"});
    let report = model.add_arrangement(&names, fixed, &[], &shown).unwrap();
    let lows = low_x(&report);
    assert_eq!(
        [lows[1] - lows[0], lows[2] - lows[1]],
        [15.0, 15.0],
        "10 mm boxes, 5 mm apart"
    );
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(step.name, "Arrange");
    let wider = model
        .edit_step_settings(
            &step.id,
            [("gap".to_owned(), "1 cm".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    let lows = low_x(&wider);
    assert_eq!([lows[1] - lows[0], lows[2] - lows[1]], [20.0, 20.0]);
    assert!(
        model
            .add_arrangement(&names[..1], json!({}), &[], &shown)
            .is_err()
    );
}

/// Spondge Holder's Combine5 lost both of its tools. A repair replaces every lost tool at once: one
/// replacement alone would leave a step that still cannot rebuild.
#[test]
fn a_combine_that_lost_two_tools_is_repaired_with_both_replacements_at_once() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let mut shown = HashMap::new();
    shown.insert(
        model.payload["bodies"][0]["id"]
            .as_str()
            .unwrap()
            .to_owned(),
        Rc::new(solid),
    );
    let make = |model: &mut Model, shown: &mut HashMap<String, Shape>, x: &str| {
        let (name, shape) = scad::rebuild::create_primitive(
            model,
            "box",
            json!({"width": "2 mm", "depth": "2 mm", "height": "20 mm", "x": x, "y": "4 mm", "z": "-5 mm"}),
        )
        .unwrap();
        let id = model.payload["bodies"]
            .as_array()
            .unwrap()
            .iter()
            .find(|b| b["name"] == name)
            .unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned();
        shown.insert(id.clone(), Rc::new(shape));
        (name, id)
    };
    let (first, _) = make(&mut model, &mut shown, "1 mm");
    let (second, _) = make(&mut model, &mut shown, "6 mm");
    let inputs = json!({"tools": [first, second], "operation": "cut", "keep_tool": true});
    model
        .add_body_operation("boolean", "Box", inputs, &[], &shown)
        .unwrap();
    let (_, spare_a) = make(&mut model, &mut shown, "1 mm");
    let (_, spare_b) = make(&mut model, &mut shown, "6 mm");
    let combine = model.payload["features"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|f| f["type"] == "boolean")
        .unwrap();
    for tool in [&first, &second] {
        combine["bindings"][tool]["feature_id"] = json!("");
    }
    let id = combine["id"].as_str().unwrap().to_owned();
    let step = model.steps().into_iter().find(|s| s.id == id).unwrap();
    assert_eq!(step.state, "needs_attention");
    let missing = step.missing_input.unwrap();
    assert_eq!(step.more_missing.len(), 1, "both lost tools are offered");
    let other = &step.more_missing[0];
    let only_one =
        model.repair_reference(&id, &missing.object_id, &spare_a, None, &[], &[], &shown);
    assert_eq!(
        only_one.err().as_deref(),
        Some("Choose a replacement for each missing input.")
    );
    let both = [(other.object_id.clone(), spare_b.clone())];
    let repaired = model
        .repair_reference(&id, &missing.object_id, &spare_a, None, &both, &[], &shown)
        .unwrap();
    assert!(!repaired.failures.contains_key(&id));
    // Two 2 × 2 mm columns through the 10 mm cube: 1000 - 2 × 40.
    let cube = repaired.bodies.iter().find(|b| b.name == "Box").unwrap();
    assert!((occt::volume(cube.shape.as_ref().unwrap()) - 920.0).abs() < 1e-6);
    let step = model.steps().into_iter().find(|s| s.id == id).unwrap();
    assert_eq!(
        (step.state.as_str(), step.missing_input.is_none()),
        ("ok", true)
    );
}

#[test]
fn a_section_replace_sets_the_replacement_into_the_target_face_and_uses_it_up() {
    let plate = occt::make_box(40.0, 40.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&plate).unwrap()).unwrap();
    let (insert, block) = scad::rebuild::create_primitive(
        &mut model,
        "box",
        json!({"width": "10 mm", "depth": "10 mm", "height": "5 mm", "x": "100 mm"}),
    )
    .unwrap();
    let id = |model: &Model, name: &str| {
        model.payload["bodies"]
            .as_array()
            .unwrap()
            .iter()
            .find(|b| b["name"] == name)
            .unwrap()["id"]
            .as_str()
            .unwrap()
            .to_owned()
    };
    let face = |shape: &occt::Shape, z: f64| {
        (0..occt::faces(shape).unwrap().len() as u32)
            .find(|&i| {
                let f = occt::face_fingerprint(shape, i).unwrap();
                f.geometry == "plane" && (f.center[2] - z).abs() < 1e-9
            })
            .unwrap()
    };
    let (top, bottom) = (face(&plate, 10.0), face(&block, 0.0));
    let shown = HashMap::from([
        (id(&model, "Box"), Rc::new(plate)),
        (id(&model, &insert), Rc::new(block)),
    ]);
    let inputs = json!({"moving_anchor": "center", "target_anchor": "center", "u": "0", "v": "0",
        "boundary": "0.5 mm", "setback": "0"});
    let report = model
        .add_section_replace(("Box", top), (&insert, bottom), inputs, &[], &shown)
        .unwrap();
    let names: Vec<_> = report.bodies.iter().map(|b| b.name.as_str()).collect();
    assert_eq!(names, ["Box"], "the replacement is used up");
    // The plate less a 9 × 9 through-cut, plus the 10 × 10 × 5 block, less their 0.5 mm overlap ring.
    let volume = occt::volume(report.bodies[0].shape.as_ref().unwrap());
    assert!((volume - 15_595.0).abs() < 1e-4, "{volume}");
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (step.name.as_str(), step.state.as_str()),
        ("Section Replace", "ok")
    );
    assert_eq!(step.face_settings.unwrap()["boundary"], "0.5 mm");
    assert!(
        model
            .add_section_replace(("Box", top), ("Box", top), json!({}), &[], &shown)
            .is_err()
    );
}

#[test]
fn a_matching_thread_is_applied_to_an_existing_shaft_as_0_4_records_it() {
    let shaft = occt::make_cylinder(15.0, 30.0).unwrap();
    let before = occt::volume(&shaft);
    let mut model = Model::new_box(occt::write_brep(&shaft).unwrap()).unwrap();
    let side = (0..occt::faces(&shaft).unwrap().len() as u32)
        .find(|&i| occt::face_fingerprint(&shaft, i).unwrap().geometry == "cylinder")
        .unwrap();
    let picked = scad::align::PickedFace {
        name: "Box",
        shape: &shaft,
        index: side,
    };
    let inputs = json!({"designation": "M30", "clearance": "normal", "resize": false});
    let threaded =
        scad::rebuild::create_face_feature(&mut model, picked, "matching_thread", inputs, None)
            .unwrap();
    assert!(
        occt::volume(&threaded) < before,
        "the thread is cut into the shaft"
    );
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (step.name.as_str(), step.kind.as_str()),
        ("Matching Thread", "matching_thread")
    );
    assert_eq!(step.face_settings.unwrap()["clearance"], "normal");
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    let inputs = &saved.model["features"].as_array().unwrap().last().unwrap()["inputs"];
    assert_eq!(
        (
            &inputs["thread_modelled"],
            &inputs["thread_internal"],
            &inputs["form"]
        ),
        (&json!(false), &json!(false), &json!("printed"))
    );
}

#[test]
fn a_round_face_is_pushed_out_along_its_radius_and_its_change_edits_from_history() {
    let rod = occt::make_cylinder(10.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&rod).unwrap()).unwrap();
    let side = (0..occt::faces(&rod).unwrap().len() as u32)
        .find(|&i| occt::face_fingerprint(&rod, i).unwrap().geometry == "cylinder")
        .unwrap();
    let picked = scad::align::PickedFace {
        name: "Box",
        shape: &rod,
        index: side,
    };
    let wider = scad::rebuild::create_face_feature(
        &mut model,
        picked,
        "resize_round",
        json!({"delta": "2"}),
        None,
    )
    .unwrap();
    let pi = std::f64::consts::PI;
    assert!(
        (occt::volume(&wider) - pi * 144.0 * 20.0).abs() < 1e-6,
        "radius 10 → 12"
    );
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (step.name.as_str(), step.kind.as_str()),
        ("Resize", "resize_round")
    );
    let saved = scad::project::read(&model.save(&[]).unwrap()).unwrap();
    assert_eq!(
        saved.model["features"].as_array().unwrap().last().unwrap()["inputs"]["delta"],
        json!(2.0)
    );
    let id = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let shown = HashMap::from([(id, Rc::new(wider))]);
    let thinner = model
        .edit_step_settings(
            &step.id,
            [("delta".to_owned(), "-3".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    assert!(
        (occt::volume(thinner.bodies[0].shape.as_ref().unwrap()) - pi * 49.0 * 20.0).abs() < 1e-6,
        "radius 10 → 7"
    );
    let bad = scad::align::PickedFace {
        name: "Box",
        shape: &rod,
        index: side,
    };
    assert!(
        scad::rebuild::create_face_feature(
            &mut model,
            bad,
            "resize_round",
            json!({"delta": "0"}),
            None
        )
        .is_err()
    );
}

#[test]
fn a_saved_move_edits_its_offset_from_history_and_keeps_the_rest_as_made() {
    let solid = occt::make_box(10.0, 10.0, 10.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let moved = scad::rebuild::create_transform(
        &mut model,
        "Box",
        &solid,
        "Move",
        json!({"dx": "5 mm", "dy": "0", "dz": "0"}),
    )
    .unwrap();
    assert_eq!(occt::bounds(&moved).unwrap()[0].round(), 5.0);
    let step = model.steps().into_iter().last().unwrap();
    let settings = step.face_settings.unwrap();
    assert_eq!(
        (settings["dx"].as_str(), settings.contains_key("rx")),
        ("5 mm", false),
        "only what the step has"
    );
    let id = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let shown = HashMap::from([(id, Rc::new(moved))]);
    let edited = model
        .edit_step_settings(
            &step.id,
            [("dx".to_owned(), "2 cm".to_owned())].into(),
            &[],
            &shown,
        )
        .unwrap();
    assert_eq!(
        occt::bounds(edited.bodies[0].shape.as_ref().unwrap()).unwrap()[0].round(),
        20.0
    );
}

#[test]
fn a_vent_plate_is_made_from_the_shapes_and_a_vent_is_cut_into_a_face() {
    let solid = occt::make_box(40.0, 40.0, 4.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&solid).unwrap()).unwrap();
    let (name, plate) = scad::rebuild::create_primitive(
        &mut model,
        "vent_plate",
        json!({"width": "60 mm", "depth": "60 mm", "thickness": "3 mm", "across_flats": "5 mm", "wall": "1.2 mm", "margin": "2 mm", "x": "100 mm"}),
    )
    .unwrap();
    assert_eq!(name, "Vent");
    let full = 60.0 * 60.0 * 3.0;
    assert!(
        occt::volume(&plate) < 0.6 * full && occt::volume(&plate) > 0.1 * full,
        "a plate full of hexagonal holes"
    );

    let top = (0..occt::faces(&solid).unwrap().len() as u32)
        .find(|&i| occt::face_fingerprint(&solid, i).unwrap().center[2] > 3.9)
        .unwrap();
    let picked = scad::align::PickedFace {
        name: "Box",
        shape: &solid,
        index: top,
    };
    let inputs = json!({"across_flats": "5 mm", "wall": "1.2 mm", "margin": "2 mm"});
    let vented =
        scad::rebuild::create_face_feature(&mut model, picked, "vent_cut", inputs, None).unwrap();
    // Hexagonal holes through the 4 mm plate take about a third of it, inside the 2 mm border.
    let ratio = occt::volume(&vented) / occt::volume(&solid);
    assert!((0.5..0.8).contains(&ratio), "{ratio}");
    let step = model.steps().into_iter().last().unwrap();
    assert_eq!(
        (
            step.kind.as_str(),
            step.face_settings.unwrap()["wall"].as_str()
        ),
        ("vent_cut", "1.2 mm")
    );
}

#[test]
fn a_closed_solid_has_no_open_edges_and_a_lone_face_has_four() {
    let solid = occt::make_box(10.0, 20.0, 30.0).unwrap();
    assert_eq!(
        (occt::open_edges(&solid), occt::solid_count(&solid)),
        (0, 1)
    );
    assert_eq!(occt::open_edges(&occt::sub_face(&solid, 0).unwrap()), 4);
}
