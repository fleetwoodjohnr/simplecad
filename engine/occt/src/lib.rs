//! The OCCT operations SimpleCAD's engine uses, at the level of operations
//! rather than classes: a shape goes in, a shape or plain data comes out.
//!
//! OCCT 8's `Standard_Failure` derives from `std::exception`, so cxx turns every
//! OCCT exception thrown inside a `Result`-returning call into an `Err` holding
//! its message. A segfault is not an exception and cannot be caught here; the
//! app runs the engine in its own process for that reason.

#[cxx::bridge(namespace = "simplecad")]
pub mod ffi {
    /// A body's display mesh. Vertices are not shared between faces, so each
    /// face keeps its own normals and can be highlighted on its own.
    ///
    /// Faces and edges are numbered as OCCT's `TopExp::MapShapes` numbers
    /// them, from 0: the same order the topological naming's `index_hint` uses.
    #[derive(Debug, Default)]
    pub struct MeshBuffers {
        /// x, y, z per vertex.
        pub positions: Vec<f32>,
        /// Unit normal per vertex, outward from the solid.
        pub normals: Vec<f32>,
        /// Three vertex indices per triangle, counter-clockwise seen from outside.
        pub indices: Vec<u32>,
        /// Face `i` owns triangles `face_tri_start[i]..face_tri_start[i + 1]`.
        pub face_tri_start: Vec<u32>,
        /// x, y, z per point of every edge polyline, end to end.
        pub edge_points: Vec<f32>,
        /// Edge `i` owns points `edge_start[i]..edge_start[i + 1]`.
        pub edge_start: Vec<u32>,
    }

    /// One face of a shape, in `TopExp::MapShapes` order like the mesh's faces.
    #[derive(Debug, Default, Clone, Copy)]
    pub struct FaceData {
        /// Only a planar face can be pushed or pulled.
        pub planar: bool,
        /// Centre of mass of the face.
        pub center: [f64; 3],
        /// Outward unit normal of a planar face; zero for a curved one.
        pub normal: [f64; 3],
        /// mm².
        pub area: f64,
    }

    /// A face as the Python app's topological naming describes it
    /// (`naming._face_fingerprint`): what survives small edits.
    #[derive(Debug, Default, Clone)]
    pub struct FaceFingerprint {
        /// plane, cylinder, cone, sphere, torus, bezier, bspline or surface.
        pub geometry: String,
        /// Area, mm².
        pub measure: f64,
        pub center: [f64; 3],
        /// The surface axis, outward for the face's orientation (planes:
        /// without the left-handed correction `faces` applies, as Python does).
        pub has_direction: bool,
        pub direction: [f64; 3],
        pub has_radius: bool,
        pub radius: f64,
    }

    /// Edge geometry and the surfaces meeting at it, for saved edge references.
    pub struct EdgeFingerprint {
        pub curve: FaceFingerprint,
        pub neighbours: Vec<FaceFingerprint>,
    }

    pub struct EdgeFinishSpec {
        pub index: u32,
        pub first: f64,
        pub mode: u8,
        pub second: f64,
        pub side: i32,
        pub flip: bool,
    }

    pub struct SplitParts {
        pub below: UniquePtr<Shape>,
        pub above: UniquePtr<Shape>,
    }

    pub struct StepBody {
        pub name: String,
        pub shape: UniquePtr<Shape>,
    }

    pub struct HoleCut {
        pub position: [f64; 3],
        pub inward: [f64; 3],
        pub diameter: f64,
        pub depth: f64,
        pub cap_diameter: f64,
        pub cap_depth: f64,
        pub tapered: bool,
    }

    /// A face as `detect.analyse_plane` and `align.planar_frame` read it.
    #[derive(Debug, Default, Clone, Copy)]
    pub struct PlaneFrame {
        /// False for a face that is not planar; the rest is then zero.
        pub planar: bool,
        pub center: [f64; 3],
        /// Outward unit normal.
        pub normal: [f64; 3],
        pub area: f64,
        /// The plane's own position: origin, X and Y directions.
        pub origin: [f64; 3],
        pub x_dir: [f64; 3],
        pub y_dir: [f64; 3],
        /// `BRepTools::UVBounds`: umin, umax, vmin, vmax.
        pub uv: [f64; 4],
    }

    /// A face as `detect.analyse_cylinder` reads it.
    #[derive(Debug, Default, Clone, Copy)]
    pub struct CylinderData {
        /// False for a face that is not cylindrical; the rest is then zero.
        pub cylindrical: bool,
        pub radius: f64,
        /// A point on the axis, at the face's low end.
        pub origin: [f64; 3],
        pub direction: [f64; 3],
        pub length: f64,
        /// A hole rather than a shaft.
        pub internal: bool,
    }

    unsafe extern "C++" {
        include!("shim.hpp");

        /// An OCCT shape: a `TopoDS_Shape`, owned.
        type Shape;

        fn make_box(dx: f64, dy: f64, dz: f64) -> Result<UniquePtr<Shape>>;
        fn make_cylinder(radius: f64, height: f64) -> Result<UniquePtr<Shape>>;
        /// Sweep the closed radial/axial profile along a cylindrical helix.
        fn sweep_thread_tooth(
            radius: f64,
            pitch: f64,
            length: f64,
            left_hand: bool,
            profile: &[f64],
        ) -> Result<UniquePtr<Shape>>;
        fn make_sphere(radius: f64) -> Result<UniquePtr<Shape>>;
        fn make_cone(bottom: f64, top: f64, height: f64) -> Result<UniquePtr<Shape>>;
        fn make_torus(major: f64, minor: f64) -> Result<UniquePtr<Shape>>;
        fn make_wedge(dx: f64, dy: f64, dz: f64, top: f64) -> Result<UniquePtr<Shape>>;
        fn polygon_profile(points: &[f64], height: f64) -> Result<UniquePtr<Shape>>;
        fn oval(width: f64, depth: f64, height: f64) -> Result<UniquePtr<Shape>>;
        fn pill(width: f64, depth: f64, height: f64) -> Result<UniquePtr<Shape>>;
        fn crescent(size: f64, height: f64, thickness: f64) -> Result<UniquePtr<Shape>>;
        /// A pipe: a cylinder with a concentric bore, cut as the Python app cuts it.
        fn make_tube(outer: f64, inner: f64, height: f64) -> Result<UniquePtr<Shape>>;
        /// A primitive's placement: `[x, y, z, rx, ry, rz]`, the turns in degrees
        /// about the world X, Y and Z axes after the move, geometry copied.
        fn place(shape: &Shape, placement: &[f64]) -> Result<UniquePtr<Shape>>;
        /// `MoveFeature`: `[dx, dy, dz, rx, ry, rz, pivot x, y, z, xyz order (1/0),
        /// has axis (1/0), axis x, y, z, angle]`, turns in degrees, applied as a
        /// placement (geometry shared), as the Python app moves a body.
        fn move_shape(shape: &Shape, placement: &[f64]) -> Result<UniquePtr<Shape>>;
        fn scale_shape(
            shape: &Shape,
            pivot: &[f64],
            factors: &[f64],
            uniform: bool,
        ) -> Result<UniquePtr<Shape>>;
        /// `ShellFeature`'s thick solid with faces `faces` opened, inward by
        /// `thickness`; the error "unusable" when OCCT gives no hollow body.
        fn shell(shape: &Shape, faces: &[u32], thickness: f64) -> Result<UniquePtr<Shape>>;
        fn fuse(a: &Shape, b: &Shape) -> Result<UniquePtr<Shape>>;
        /// Merge the coplanar faces and collinear edges a boolean leaves.
        fn unify(shape: &Shape) -> Result<UniquePtr<Shape>>;
        /// OCCT's text B-Rep format, as `.scad3` files store it.
        fn read_brep(bytes: &[u8]) -> Result<UniquePtr<Shape>>;
        fn write_brep(shape: &Shape) -> Result<Vec<u8>>;
        /// Read independently editable solids or shells, in STEP root order, in mm.
        fn read_step(bytes: &[u8], fallback_name: &str) -> Result<Vec<StepBody>>;
        /// Write one analytic STEP transfer root per object in order, as AP214 in mm.
        fn write_step(shapes: &Shapes) -> Result<Vec<u8>>;
        /// The shape placed by a 3×4 matrix, row by row (`Shape.Located`, as
        /// the Python app places a cached "located" output).
        fn located(shape: &Shape, matrix: &[f64]) -> Result<UniquePtr<Shape>>;
        fn volume(shape: &Shape) -> f64;
        fn solid_count(shape: &Shape) -> u32;
        /// Faces as `TopExp_Explorer` visits them, a face used twice counted
        /// twice: how the Python app's project comparison counts.
        fn face_occurrences(shape: &Shape) -> u32;
        /// `BRepCheck_Analyzer`'s verdict.
        fn is_valid(shape: &Shape) -> Result<bool>;
        /// The optimal bounding box: min x, y, z then max x, y, z.
        fn bounds(shape: &Shape) -> Result<Vec<f64>>;
        fn faces(shape: &Shape) -> Result<Vec<FaceData>>;
        /// Face `face` (a `faces` index) as the naming fingerprints it.
        fn face_fingerprint(shape: &Shape, face: u32) -> Result<FaceFingerprint>;
        fn edge_fingerprints(shape: &Shape) -> Result<Vec<EdgeFingerprint>>;
        fn finish_edge_specs(
            shape: &Shape,
            specs: &[EdgeFinishSpec],
            fillet: bool,
        ) -> Result<UniquePtr<Shape>>;
        fn finish_edges(
            shape: &Shape,
            indices: &[u32],
            size: f64,
            fillet: bool,
        ) -> Result<UniquePtr<Shape>>;
        /// Move planar face `face` (a `faces` index) `distance` mm along its
        /// outward normal, taking its recesses and bosses with it. The same
        /// operation, and the same messages, as the Python engine's
        /// `PushPullFeature`.
        fn push_pull(shape: &Shape, face: u32, distance: f64) -> Result<UniquePtr<Shape>>;
        fn resize_round(shape: &Shape, face: u32, delta: f64) -> Result<UniquePtr<Shape>>;
        fn split_solid(shape: &Shape, point: &[f64], normal: &[f64]) -> Result<SplitParts>;
        fn hole_cut(shape: &Shape, cut: &HoleCut) -> Result<UniquePtr<Shape>>;
        fn first_ray_hit(shape: &Shape, position: &[f64], inward: &[f64]) -> Result<f64>;
        /// Mesh with an absolute chord `deflection` (mm) and `angle` (radians).
        fn mesh(shape: &Shape, deflection: f64, angle: f64) -> Result<MeshBuffers>;
        /// A `gp_Trsf`, composed in C++ as OCP composes it.
        type Trsf;
        fn trsf_identity() -> UniquePtr<Trsf>;
        fn trsf_mirror(origin: &[f64], normal: &[f64]) -> Result<UniquePtr<Trsf>>;
        /// A turn of `angle` radians about the axis through `pivot`.
        fn trsf_rotation(pivot: &[f64], axis: &[f64], angle: f64) -> Result<UniquePtr<Trsf>>;
        fn trsf_translation(by: &[f64]) -> UniquePtr<Trsf>;
        fn trsf_axis_frame(origin: &[f64], normal: &[f64]) -> Result<UniquePtr<Trsf>>;
        /// `a.Multiplied(b)`: `b`, then `a`.
        fn trsf_multiplied(a: &Trsf, b: &Trsf) -> UniquePtr<Trsf>;
        fn trsf_point(trsf: &Trsf, point: &[f64]) -> Vec<f64>;
        /// The 3×4 matrix, row by row.
        fn trsf_values(trsf: &Trsf) -> Vec<f64>;
        /// `occ.transformed`: geometry copied, or only relocated.
        fn transform_shape(shape: &Shape, trsf: &Trsf, copy: bool) -> Result<UniquePtr<Shape>>;
        /// Face `face` (a `faces` index) on its own.
        fn sub_face(shape: &Shape, face: u32) -> Result<UniquePtr<Shape>>;
        fn plane_frame(face: &Shape) -> Result<PlaneFrame>;
        fn cylinder_data(face: &Shape) -> Result<CylinderData>;
        /// `align._x_extent`: the X range of the exact optimal bounding box.
        fn x_extent(shape: &Shape) -> Result<Vec<f64>>;
        /// Shapes passed together, in order: a boolean's tools.
        type Shapes;
        fn shapes_new() -> UniquePtr<Shapes>;
        fn shapes_push(shapes: Pin<&mut Shapes>, shape: &Shape);
        fn shapes_compound(shapes: &Shapes) -> UniquePtr<Shape>;
        fn fuse_copies(shape: &Shape, copies: &Shapes) -> Result<UniquePtr<Shape>>;
        fn draft_angle(
            shape: &Shape,
            faces: &[u32],
            origin: &[f64],
            pull: &[f64],
            radians: f64,
        ) -> Result<UniquePtr<Shape>>;
        /// `BooleanFeature`'s boolean: join (0), cut (1) or intersect (2) of
        /// `target` with `tools`, with an optional fuzzy value.
        fn combine(
            operation: u8,
            target: &Shape,
            tools: &Shapes,
            fuzzy: f64,
        ) -> Result<UniquePtr<Shape>>;
        /// The distinct solids in a shape (`_join_solids`).
        fn distinct_solids(shape: &Shape) -> u32;
        /// `_join_bridge_gap`: the tolerance that connects every solid, 0 when they
        /// touch, or -1 when one is more than 0.2 mm from the rest.
        fn join_bridge_gap(shape: &Shape) -> f64;
        /// Dereferences null on purpose, so the crash isolation can be drilled.
        fn crash();
    }
}

pub use cxx::{Exception, UniquePtr};
pub use ffi::*;

// A shape is plain data with no thread affinity; OCCT's handles are
// atomically reference counted.
unsafe impl Send for ffi::Shape {}
unsafe impl Sync for ffi::Shape {}
unsafe impl Send for ffi::Trsf {}
unsafe impl Send for ffi::Shapes {}
unsafe impl Sync for ffi::Trsf {}

#[cfg(test)]
mod tests {
    use super::ffi;

    #[test]
    fn a_box_has_its_volume_and_survives_brep_round_trip() {
        let shape = ffi::make_box(10.0, 20.0, 30.0).unwrap();
        assert!((ffi::volume(&shape) - 6000.0).abs() < 1e-9);
        let bytes = ffi::write_brep(&shape).unwrap();
        // OCCT 8 starts the stream with a newline; files the Python app saved read the same.
        assert!(bytes.trim_ascii_start().starts_with(b"CASCADE Topology"));
        let back = ffi::read_brep(&bytes).unwrap();
        assert!((ffi::volume(&back) - 6000.0).abs() < 1e-9);
    }

    #[test]
    fn step_round_trip_preserves_body_order_size_and_placement() {
        let first = ffi::make_box(10.0, 20.0, 30.0).unwrap();
        let box_at_origin = ffi::make_box(4.0, 5.0, 6.0).unwrap();
        let second = ffi::place(&box_at_origin, &[50.0, 0.0, 0.0, 0.0, 0.0, 0.0]).unwrap();
        let mut shapes = ffi::shapes_new();
        ffi::shapes_push(shapes.pin_mut(), &first);
        ffi::shapes_push(shapes.pin_mut(), &second);

        let bytes = ffi::write_step(&shapes).unwrap();
        assert!(bytes.starts_with(b"ISO-10303-21;"));
        let bodies = ffi::read_step(&bytes, "Assembly").unwrap();

        assert_eq!(bodies.len(), 2);
        for (body, (source, name)) in bodies
            .iter()
            .zip([(&first, "Assembly 1"), (&second, "Assembly 2")])
        {
            assert_eq!(body.name, name);
            assert!((ffi::volume(&body.shape) - ffi::volume(source)).abs() < 1e-6);
            for (actual, expected) in ffi::bounds(&body.shape)
                .unwrap()
                .into_iter()
                .zip(ffi::bounds(source).unwrap())
            {
                assert!((actual - expected).abs() < 1e-6);
            }
        }
    }

    #[test]
    fn fuse_then_unify_leaves_one_face_per_side() {
        let a = ffi::make_box(10.0, 10.0, 10.0).unwrap();
        let moved = ffi::read_brep(&ffi::write_brep(&a).unwrap()).unwrap();
        let fused = ffi::fuse(&a, &moved).unwrap();
        let tidy = ffi::unify(&fused).unwrap();
        assert!((ffi::volume(&tidy) - 1000.0).abs() < 1e-9);
        assert_eq!(
            ffi::mesh(&tidy, 0.05, 0.35).unwrap().face_tri_start.len(),
            7
        );
    }

    #[test]
    fn a_box_meshes_as_six_faces_twelve_triangles_twelve_edges() {
        let shape = ffi::make_box(10.0, 20.0, 30.0).unwrap();
        let mesh = ffi::mesh(&shape, 0.05, 0.35).unwrap();
        assert_eq!(mesh.face_tri_start.len(), 7);
        assert_eq!(*mesh.face_tri_start.last().unwrap(), 12);
        assert_eq!(mesh.indices.len(), 36);
        assert_eq!(mesh.edge_start.len(), 13);
        assert_eq!(mesh.positions.len(), mesh.normals.len());
        // Every normal is a unit vector.
        for n in mesh.normals.chunks(3) {
            let length = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
            assert!((length - 1.0).abs() < 1e-4, "{n:?}");
        }
        // Winding agrees with the normals: each triangle faces the way its
        // vertices' normals point.
        for t in mesh.indices.chunks(3) {
            let p = |i: u32| {
                let i = i as usize * 3;
                [
                    mesh.positions[i],
                    mesh.positions[i + 1],
                    mesh.positions[i + 2],
                ]
            };
            let (a, b, c) = (p(t[0]), p(t[1]), p(t[2]));
            let u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
            let v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
            let cross = [
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            ];
            let n = t[0] as usize * 3;
            let dot = cross[0] * mesh.normals[n]
                + cross[1] * mesh.normals[n + 1]
                + cross[2] * mesh.normals[n + 2];
            assert!(dot > 0.0);
        }
    }

    #[test]
    fn an_occt_exception_comes_back_as_an_error_with_its_message() {
        let error = ffi::make_box(0.0, 1.0, 1.0)
            .err()
            .expect("a zero-size box must fail");
        assert!(!error.what().is_empty());
        assert!(ffi::read_brep(b"not a brep").is_err());
    }
}
