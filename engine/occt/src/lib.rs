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

    unsafe extern "C++" {
        include!("shim.hpp");

        /// An OCCT shape: a `TopoDS_Shape`, owned.
        type Shape;

        fn make_box(dx: f64, dy: f64, dz: f64) -> Result<UniquePtr<Shape>>;
        fn fuse(a: &Shape, b: &Shape) -> Result<UniquePtr<Shape>>;
        /// Merge the coplanar faces and collinear edges a boolean leaves.
        fn unify(shape: &Shape) -> Result<UniquePtr<Shape>>;
        /// OCCT's text B-Rep format, as `.scad3` files store it.
        fn read_brep(bytes: &[u8]) -> Result<UniquePtr<Shape>>;
        fn write_brep(shape: &Shape) -> Result<Vec<u8>>;
        fn volume(shape: &Shape) -> f64;
        fn solid_count(shape: &Shape) -> u32;
        /// `BRepCheck_Analyzer`'s verdict.
        fn is_valid(shape: &Shape) -> Result<bool>;
        /// The optimal bounding box: min x, y, z then max x, y, z.
        fn bounds(shape: &Shape) -> Result<Vec<f64>>;
        fn faces(shape: &Shape) -> Result<Vec<FaceData>>;
        /// Move planar face `face` (a `faces` index) `distance` mm along its
        /// outward normal, taking its recesses and bosses with it. The same
        /// operation, and the same messages, as the Python engine's
        /// `PushPullFeature`.
        fn push_pull(shape: &Shape, face: u32, distance: f64) -> Result<UniquePtr<Shape>>;
        /// Mesh with an absolute chord `deflection` (mm) and `angle` (radians).
        fn mesh(shape: &Shape, deflection: f64, angle: f64) -> Result<MeshBuffers>;
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
