//! Push/pull on the shapes the Python engine's tests use (`tests/test_operations.py`
//! `_wall_with_features` and the dumbbell), written out as B-Rep fixtures, with the
//! same expectations.

use occt::ffi;
use std::f64::consts::PI;

fn fixture(name: &str) -> cxx::UniquePtr<ffi::Shape> {
    let path = format!("{}/tests/fixtures/{name}.brep", env!("CARGO_MANIFEST_DIR"));
    ffi::read_brep(&std::fs::read(path).unwrap()).unwrap()
}

/// The largest planar face whose normal is +Z at height `z`.
fn top_face(shape: &ffi::Shape, z: f64) -> u32 {
    let faces = ffi::faces(shape).unwrap();
    let (index, _) = faces
        .iter()
        .enumerate()
        .filter(|(_, f)| f.planar && f.normal[2] > 0.99 && (f.center[2] - z).abs() < 1e-6)
        .max_by(|a, b| a.1.area.total_cmp(&b.1.area))
        .expect("no such face");
    index as u32
}

#[test]
fn a_recess_and_a_boss_move_with_the_face_and_holes_stay_through() {
    let before = fixture("wall_with_features");
    let face = top_face(&before, 20.0);
    for distance in [-6.0, 6.0] {
        let after = ffi::push_pull(&before, face, distance).unwrap();
        assert!(ffi::is_valid(&after).unwrap());
        assert_eq!(ffi::solid_count(&after), 1);
        // Every column under the face grows or shrinks by the distance, except
        // the two through-holes (r 3 and r 2), which stay empty.
        let expected = ffi::volume(&before) + distance * (60.0 * 60.0 - 13.0 * PI);
        let got = ffi::volume(&after);
        assert!(
            (got - expected).abs() < 1e-6 * expected,
            "{distance}: {got} vs {expected}"
        );
        // The boss still stands 5 tall on the moved face.
        let top = ffi::bounds(&after).unwrap()[5];
        assert!(
            (top - (25.0 + distance)).abs() < 1e-6,
            "{distance}: top {top}"
        );
    }
}

#[test]
fn a_push_that_would_cut_the_part_in_two_is_refused() {
    let shape = fixture("dumbbell");
    let bar = top_face(&shape, 4.0);
    let error = ffi::push_pull(&shape, bar, -5.0).err().unwrap();
    assert!(error.what().contains("2 pieces"), "{}", error.what());
}

#[test]
fn a_push_deeper_than_the_body_is_refused_and_curved_faces_are_not_pushed() {
    let shape = fixture("wall_with_features");
    let face = top_face(&shape, 20.0);
    let error = ffi::push_pull(&shape, face, -30.0).err().unwrap();
    assert!(
        error.what().contains("straight through"),
        "{}",
        error.what()
    );

    let faces = ffi::faces(&shape).unwrap();
    let curved = faces.iter().position(|f| !f.planar).unwrap() as u32;
    let error = ffi::push_pull(&shape, curved, 1.0).err().unwrap();
    assert!(error.what().contains("flat faces"), "{}", error.what());
}

#[test]
fn a_plain_face_pull_merges_into_one_face_per_side() {
    let shape = ffi::make_box(10.0, 10.0, 10.0).unwrap();
    let top = top_face(&shape, 10.0);
    let after = ffi::push_pull(&shape, top, 5.0).unwrap();
    assert!((ffi::volume(&after) - 1500.0).abs() < 1e-9);
    assert_eq!(ffi::faces(&after).unwrap().len(), 6);
}
