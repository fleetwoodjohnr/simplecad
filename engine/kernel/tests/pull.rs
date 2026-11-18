use scad_kernel::{Boolean, Bounds, Solid};

fn block(min: [f64; 3], max: [f64; 3]) -> Solid {
    Solid::cuboid(Bounds { min, max }).unwrap()
}

fn wall() -> Solid {
    block([0.; 3], [30., 20., 10.])
        .boolean(&block([4., 4., 8.], [12., 12., 11.]), Boolean::Subtract)
        .unwrap()
        .boolean(&block([6., 6., -1.], [8., 8., 9.]), Boolean::Subtract)
        .unwrap()
        .boolean(&block([20., 4., -1.], [24., 8., 11.]), Boolean::Subtract)
        .unwrap()
        .boolean(&block([15., 12., 9.], [18., 16., 13.]), Boolean::Union)
        .unwrap()
}

#[test]
fn push_and_pull_carry_a_recess_and_boss_and_keep_holes_open() {
    let original = wall();
    let face = original
        .faces()
        .iter()
        .position(|face| face.axis == 2 && face.positive && face.plane == 10.)
        .unwrap();
    for distance in [-3., 3.] {
        let moved = original.push_pull(face, distance).unwrap();
        assert_eq!(moved.volume(), original.volume() + (600. - 20.) * distance);
        assert!(moved.contains([5., 5., 7.5 + distance]));
        assert!(!moved.contains([5., 5., 8.5 + distance]));
        assert!(moved.contains([16., 13., 12.5 + distance]));
        assert!(!moved.contains([7., 7., 6. + distance]));
        assert!(!moved.contains([21., 5., 6. + distance]));
        assert_eq!(moved.solid_count(), 1);
    }
    assert!(original.push_pull(face, -9.).is_err());
    assert_eq!(original.solid_count(), 1);
}

#[test]
fn rejecting_contact_along_an_edge_avoids_a_nonmanifold_solid() {
    let a = block([0.; 3], [1.; 3]);
    let b = block([1., 1., 0.], [2., 2., 1.]);
    assert!(a.boolean(&b, Boolean::Union).is_err());
}

#[test]
fn shrinking_a_negative_face_moves_in_its_normal_direction() {
    let original = block([0.; 3], [30., 20., 10.]);
    let face = original
        .faces()
        .iter()
        .position(|face| face.axis == 0 && !face.positive)
        .unwrap();
    let moved = original.push_pull(face, -5.).unwrap();
    assert_eq!(moved.volume(), 5000.);
    assert!(!moved.contains([4., 10., 5.]));
}

#[test]
fn repeated_pulls_discard_old_planes_from_the_display_mesh() {
    let mut shape = block([0.; 3], [30., 20., 10.]);
    for _ in 0..250 {
        let top = shape
            .faces()
            .iter()
            .position(|face| face.axis == 2 && face.positive)
            .unwrap();
        shape = shape.push_pull(top, 1.).unwrap();
    }
    let (faces, mesh) = shape.boundary_mesh();
    assert_eq!(shape.volume(), 156000.);
    assert_eq!(faces.len(), 6);
    assert_eq!(mesh.indices.len(), 36);
    assert_eq!(mesh.edge_start.len(), 13);
}

#[test]
fn outer_pull_then_inner_push_lengthens_an_enclosure_with_a_recessed_vent() {
    let original = scad_kernel::examples::recessed_enclosure().unwrap();
    let go = |shape: &Solid, positive, plane, distance| {
        let face = shape
            .faces()
            .iter()
            .position(|face| face.axis == 1 && face.positive == positive && face.plane == plane)
            .unwrap();
        shape.push_pull(face, distance).unwrap()
    };
    let wall = original
        .faces()
        .into_iter()
        .find(|face| face.axis == 1 && !face.positive && face.plane == 76.)
        .unwrap();
    let [x, y, z] = wall.anchor;
    assert!(original.contains([x, y + 0.25, z]));
    let pulled = go(&original, true, 80., 50.75);
    let enclosure = go(&pulled, false, 76., -49.75);
    assert_eq!(enclosure.solid_count(), 1);
    assert!(!enclosure.contains([30., 125.5, 20.]));
    assert!(enclosure.contains([30., 126., 20.]));
    assert!(!enclosure.contains([45., 127.5, 20.]));
    assert!(enclosure.contains([45., 128., 20.]));
    assert!(!enclosure.contains([51., 130., 21.]));
    assert!(!enclosure.contains([16., 130., 46.]));
    assert!(enclosure.contains([30., 2., 20.]));
}
