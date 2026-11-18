use napi::bindgen_prelude::*;
use napi_derive::napi;
use scad_kernel::{Bounds, Solid, examples};

#[napi]
pub struct Shape {
    solid: Solid,
}

#[napi(object)]
pub struct FaceInfo {
    pub center: Vec<f64>,
    pub normal: Vec<f64>,
    pub area: f64,
}

#[napi(object)]
pub struct DisplayShape {
    pub volume: f64,
    pub faces: Vec<FaceInfo>,
    pub positions: Float32Array,
    pub normals: Float32Array,
    pub indices: Uint32Array,
    pub face_tri_start: Uint32Array,
    pub edge_points: Float32Array,
    pub edge_start: Uint32Array,
}

fn box_between(min: [f64; 3], max: [f64; 3]) -> std::result::Result<Solid, &'static str> {
    Solid::cuboid(Bounds { min, max })
}

#[napi]
impl Shape {
    #[napi(factory)]
    pub fn make_box(dx: f64, dy: f64, dz: f64) -> Result<Self> {
        box_between([0.; 3], [dx, dy, dz])
            .map(|solid| Self { solid })
            .map_err(Error::from_reason)
    }

    #[napi(factory)]
    pub fn recessed_wall_example() -> Result<Self> {
        examples::recessed_enclosure()
            .map(|solid| Self { solid })
            .map_err(Error::from_reason)
    }

    #[napi]
    pub fn push_pull(&self, face_id: u32, distance: f64) -> Result<Self> {
        self.solid
            .push_pull(face_id as usize, distance)
            .map(|solid| Self { solid })
            .map_err(Error::from_reason)
    }

    #[napi]
    pub fn display(&self) -> DisplayShape {
        let (faces, mesh) = self.solid.boundary_mesh();
        DisplayShape {
            volume: self.solid.volume(),
            faces: faces
                .into_iter()
                .map(|face| {
                    let mut normal = vec![0.; 3];
                    normal[face.axis] = if face.positive { 1. } else { -1. };
                    FaceInfo {
                        center: face.center.into(),
                        normal,
                        area: face.area,
                    }
                })
                .collect(),
            positions: mesh.positions.into(),
            normals: mesh.normals.into(),
            indices: mesh.indices.into(),
            face_tri_start: mesh.face_tri_start.into(),
            edge_points: mesh.edge_points.into(),
            edge_start: mesh.edge_start.into(),
        }
    }
}

#[napi]
pub fn read_project(data: Buffer) -> Result<String> {
    scad_kernel::project::read_model(&data).map_err(Error::from_reason)
}

#[napi]
pub fn write_project(model: String) -> Result<Buffer> {
    scad_kernel::project::write_model(&model)
        .map(Buffer::from)
        .map_err(Error::from_reason)
}
