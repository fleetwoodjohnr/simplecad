//! An Undo that brings back a body 0.4.4 saved whole after a Move: it is the shown body moved, so it comes back as
//! that body placed there, sharing its geometry, and the window moves the display it has instead of meshing again.
use std::rc::Rc;

fn points(shape: &occt::Shape) -> Vec<[f64; 3]> {
    occt::vertex_fingerprints(shape)
        .unwrap()
        .iter()
        .map(|v| v.center)
        .collect()
}

#[test]
fn a_body_saved_whole_after_a_move_comes_back_as_the_shown_one_moved() {
    let shown = Rc::new(occt::make_box(40.0, 30.0, 20.0).unwrap());
    // As 0.4.4 saved it: the box turned 30° about Z and moved, copied, written and read back as geometry of its own.
    let turn = occt::trsf_rotation(&[0.0, 0.0, 0.0], &[0.0, 0.0, 1.0], 30f64.to_radians()).unwrap();
    let turned = occt::transform_shape(&shown, &turn, true).unwrap();
    let moved =
        occt::transform_shape(&turned, &occt::trsf_translation(&[5.0, -7.0, 2.5]), true).unwrap();
    let restored = Rc::new(occt::read_brep(&occt::write_brep(&moved).unwrap()).unwrap());
    assert_ne!(occt::shape_partner(&shown), occt::shape_partner(&restored));

    let copy = scad::document::as_moved_copy(&shown, &restored).expect("a moved copy");
    assert_eq!(occt::shape_partner(&copy), occt::shape_partner(&shown));
    for (a, b) in points(&copy).iter().zip(points(&restored)) {
        assert!((0..3).all(|i| (a[i] - b[i]).abs() < 1e-9), "{a:?} != {b:?}");
    }
    assert!((occt::volume(&copy) - occt::volume(&restored)).abs() < 1e-6);

    // What is not the shown body moved stays as it came back.
    let other = Rc::new(occt::make_box(40.0, 30.0, 21.0).unwrap());
    assert!(scad::document::as_moved_copy(&shown, &other).is_none());
    assert!(scad::document::as_moved_copy(&shown, &shown).is_none());
}
