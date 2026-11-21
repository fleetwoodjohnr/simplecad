use occt::{
    ClipPlacementSpec, ClipSpec, Shape, bounds, clip_connectors, faces, is_valid, make_box,
    sub_face, transform_shape, trsf_translation, volume,
};

fn horizontal_face(shape: &Shape, normal_z: f64) -> occt::UniquePtr<Shape> {
    let index = faces(shape)
        .unwrap()
        .iter()
        .position(|face| (face.normal[2] - normal_z).abs() < 1e-6)
        .unwrap();
    sub_face(shape, index as u32).unwrap()
}

#[test]
fn a_printed_connector_joint_cuts_both_parts_and_outputs_separate_clips() {
    let bottom = make_box(50.0, 30.0, 12.0).unwrap();
    let top = transform_shape(&bottom, &trsf_translation(&[0.0, 0.0, 12.0]), true).unwrap();
    let top_face = horizontal_face(&top, -1.0);
    let bottom_face = horizontal_face(&bottom, 1.0);
    let spec = ClipSpec {
        arm_width: 1.2,
        slot: 0.8,
        thickness: 2.0,
        engagement: 6.0,
        bridge: 1.0,
        barb: 0.4,
        removal_angle: 35.0,
        clearance: 0.15,
        depth_clearance: 0.3,
        lead_in: 0.4,
        min_wall: 0.8,
    };
    let placement = ClipPlacementSpec {
        count: 2,
        spacing: 80.0,
        edge_offset: 1.0,
        shift: 0.0,
        rotation: 0.0,
        male_on_a: true,
    };
    let result = clip_connectors(
        &top,
        &bottom,
        &top_face,
        &bottom_face,
        &spec,
        &placement,
        &[],
    )
    .unwrap();
    assert_eq!(result.connectors.len(), 2);
    assert!(volume(&result.first) < volume(&top));
    assert!(volume(&result.second) < volume(&bottom));
    assert!(is_valid(&result.first).unwrap() && is_valid(&result.second).unwrap());
    for item in result.connectors {
        assert!(is_valid(&item.shape).unwrap() && volume(&item.shape) > 0.0);
        let extent = bounds(&item.shape).unwrap();
        assert!((extent[2] - 5.5).abs() < 1e-5 && (extent[5] - 18.5).abs() < 1e-5);
    }
}
