use occt::{
    TextureSpec, bounds, cylinder_data, face_texture, faces, is_valid, make_box, make_tube,
    solid_count, sub_face, volume,
};

fn settings() -> TextureSpec {
    TextureSpec {
        size: 5.0,
        spacing: 5.0,
        depth: 0.4,
        rotation: 0.0,
        offset_u: 0.0,
        offset_v: 0.0,
        edge_clearance: 1.0,
        hole_clearance: 2.0,
        text_clearance: 1.5,
        other_clearance: 2.0,
    }
}

#[test]
fn a_planar_crosshatch_builds_connected_relief_and_respects_the_border() {
    let box_shape = make_box(15.0, 15.0, 3.0).unwrap();
    let top = faces(&box_shape)
        .unwrap()
        .iter()
        .position(|face| face.normal[2] > 0.99)
        .unwrap();
    let face = sub_face(&box_shape, top as u32).unwrap();
    let result = face_texture(&box_shape, &face, "crosshatch", "raised", &settings(), &[]).unwrap();
    assert!(is_valid(&result).unwrap());
    assert_eq!(solid_count(&result), 1);
    assert!(volume(&result) > volume(&box_shape));
    let [x0, y0, _, x1, y1, z1]: [f64; 6] = bounds(&result).unwrap().try_into().unwrap();
    for (actual, expected) in [(x0, 0.0), (y0, 0.0), (x1, 15.0), (y1, 15.0), (z1, 3.4)] {
        assert!((actual - expected).abs() < 1e-5);
    }
}

#[test]
fn lines_wrap_around_both_sides_of_a_cylindrical_wall() {
    let tube = make_tube(7.0, 5.0, 8.0).unwrap();
    for (internal, mode, sign) in [(false, "raised", 1.0), (true, "recessed", -1.0)] {
        let face = (0..faces(&tube).unwrap().len() as u32)
            .map(|index| sub_face(&tube, index).unwrap())
            .find(|face| {
                let info = cylinder_data(face).unwrap();
                info.cylindrical && info.internal == internal
            })
            .unwrap();
        let result =
            face_texture(&tube, &face, "horizontal_lines", mode, &settings(), &[]).unwrap();
        assert!(is_valid(&result).unwrap());
        assert_eq!(solid_count(&result), 1);
        assert!(sign * (volume(&result) - volume(&tube)) > 1e-6);
    }
}
