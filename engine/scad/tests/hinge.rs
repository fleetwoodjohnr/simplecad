//! Hinges built and saved by SimpleCAD 0.4.4 (a box and its lid, joined along the box's top back edge, as its
//! tests/test_hinge.py builds them): rebuilt from the recipe alone, every body is what 0.4.4 saved.

use std::collections::{BTreeSet, HashMap};

#[test]
fn hinges_rebuild_from_their_recipes_as_simplecad_044_built_them() {
    let folder = format!("{}/tests/data/hinge", env!("CARGO_MANIFEST_DIR"));
    for kind in ["knuckle", "pin", "snap", "stop7", "living"] {
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
