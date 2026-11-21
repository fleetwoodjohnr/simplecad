use super::{Mesh, NO_FACES, Point, Triangle, build};
use base64::Engine as _;
use gltf::{Gltf, mesh::Mode};
use occt::ImportedBody;
use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

type Matrix = [[f64; 4]; 4];
const IDENTITY: Matrix = [
    [1., 0., 0., 0.],
    [0., 1., 0., 0.],
    [0., 0., 1., 0.],
    [0., 0., 0., 1.],
];

fn product(parent: Matrix, child: [[f32; 4]; 4]) -> Matrix {
    std::array::from_fn(|column| {
        std::array::from_fn(|row| {
            (0..4)
                .map(|i| parent[i][row] * f64::from(child[column][i]))
                .sum()
        })
    })
}

fn point(matrix: Matrix, position: [f32; 3]) -> Point {
    let transformed = |row: usize| {
        matrix[3][row]
            + (0..3)
                .map(|i| matrix[i][row] * f64::from(position[i]))
                .sum::<f64>()
    };
    // As the v0.4 importer does, keep the source axes and convert metres to millimetres.
    std::array::from_fn(|axis| transformed(axis) * 1000.0)
}

fn buffers(gltf: &Gltf) -> Result<Vec<Cow<'_, [u8]>>, String> {
    let mut total = 0u64;
    gltf.buffers()
        .map(|buffer| {
            let data = match buffer.source() {
                gltf::buffer::Source::Bin => {
                    Cow::Borrowed(gltf.blob.as_deref().ok_or("The GLB has no binary data.")?)
                }
                gltf::buffer::Source::Uri(uri) => {
                    let (media, content) = uri
                        .split_once(',')
                        .ok_or("The glTF references a missing buffer.")?;
                    if !matches!(
                        media,
                        "data:application/octet-stream;base64"
                            | "data:application/gltf-buffer;base64"
                    ) {
                        return Err("The glTF references an unsupported buffer.".into());
                    }
                    use base64::engine::general_purpose::{STANDARD, STANDARD_NO_PAD};
                    Cow::Owned(
                        STANDARD
                            .decode(content)
                            .or_else(|_| STANDARD_NO_PAD.decode(content))
                            .map_err(|_| "The glTF contains an invalid embedded buffer.")?,
                    )
                }
            };
            if data.len() < buffer.length() {
                return Err("The glTF buffer is incomplete.".into());
            }
            total += data.len() as u64;
            if total > crate::rebuild::MAX_IMPORT_BYTES {
                return Err("The glTF buffers exceed the import size limit.".into());
            }
            Ok(data)
        })
        .collect()
}

fn triangles(mode: Mode, indices: &[u32]) -> Result<Vec<Triangle>, String> {
    match mode {
        Mode::Triangles => {
            let (triangles, remainder) = indices.as_chunks::<3>();
            if !remainder.is_empty() {
                return Err("A glTF triangle is incomplete.".into());
            }
            Ok(triangles.to_vec())
        }
        Mode::TriangleStrip => Ok(indices
            .windows(3)
            .enumerate()
            .map(|(i, chunk)| {
                if i % 2 == 0 {
                    [chunk[0], chunk[1], chunk[2]]
                } else {
                    [chunk[1], chunk[0], chunk[2]]
                }
            })
            .collect()),
        Mode::TriangleFan => Ok(indices
            .get(1..)
            .unwrap_or_default()
            .windows(2)
            .map(|pair| [indices[0], pair[0], pair[1]])
            .collect()),
        _ => Ok(Vec::new()),
    }
}

fn make_mesh(
    source: gltf::Mesh,
    matrix: Matrix,
    buffers: &[Cow<'_, [u8]>],
) -> Result<Mesh, String> {
    let mut mesh = Mesh::default();
    let mut welded: HashMap<[u64; 3], u32> = HashMap::new();
    let [x, y, z, _] = matrix;
    let reflected = x[0] * (y[1] * z[2] - y[2] * z[1]) - x[1] * (y[0] * z[2] - y[2] * z[0])
        + x[2] * (y[0] * z[1] - y[1] * z[0])
        < 0.0;
    for primitive in source.primitives() {
        if !matches!(
            primitive.mode(),
            Mode::Triangles | Mode::TriangleStrip | Mode::TriangleFan
        ) {
            continue;
        }
        let reader = primitive.reader(|buffer| buffers.get(buffer.index()).map(AsRef::as_ref));
        let positions = reader
            .read_positions()
            .ok_or("A glTF part has no readable vertices.")?;
        let mut local = Vec::with_capacity(positions.size_hint().0);
        for vertex in positions {
            let position = point(matrix, vertex);
            if position.iter().any(|n| !n.is_finite()) {
                return Err("The glTF contains a non-finite vertex.".into());
            }
            let key = position.map(|value| if value == 0.0 { 0 } else { value.to_bits() });
            let index = *welded.entry(key).or_insert_with(|| {
                let index = mesh.points.len() as u32;
                mesh.points.push(position);
                index
            });
            local.push(index);
        }
        let indices = if primitive.indices().is_some() {
            reader
                .read_indices()
                .ok_or("A glTF part has no readable indices.")?
                .into_u32()
                .collect()
        } else {
            (0..local.len() as u32).collect::<Vec<_>>()
        };
        for mut facet in triangles(primitive.mode(), &indices)? {
            for vertex in &mut facet {
                *vertex = *local
                    .get(*vertex as usize)
                    .ok_or("A glTF face references a missing vertex.")?;
            }
            if reflected {
                facet.swap(1, 2);
            }
            mesh.triangles.push(facet);
        }
    }
    Ok(mesh)
}

pub(super) fn read(bytes: &[u8], fallback: &str) -> Result<Vec<ImportedBody>, String> {
    let gltf = Gltf::from_slice(bytes)
        .map_err(|error| format!("This glTF file could not be read. {error}"))?;
    if gltf.extensions_required().any(|_| true) {
        return Err("This glTF uses a required extension that this build cannot read.".into());
    }
    let data = buffers(&gltf)?;
    let roots: Vec<_> = if let Some(scene) = gltf.default_scene().or_else(|| gltf.scenes().next()) {
        scene.nodes().collect()
    } else {
        let children: HashSet<_> = gltf
            .nodes()
            .flat_map(|node| node.children().map(|child| child.index()))
            .collect();
        gltf.nodes()
            .filter(|node| !children.contains(&node.index()))
            .collect()
    };
    let mut pending: Vec<_> = roots
        .into_iter()
        .rev()
        .map(|node| (node, IDENTITY))
        .collect();
    let mut visited = HashSet::new();
    let mut bodies = Vec::new();
    while let Some((node, parent)) = pending.pop() {
        if !visited.insert(node.index()) {
            return Err("The glTF scene contains a cycle or a node with multiple parents.".into());
        }
        let matrix = product(parent, node.transform().matrix());
        pending.extend(
            node.children()
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .map(|child| (child, matrix)),
        );
        if let Some(source) = node.mesh() {
            let label = node
                .name()
                .or_else(|| source.name())
                .map(String::from)
                .unwrap_or_else(|| format!("{fallback} {}", bodies.len() + 1));
            let mesh = make_mesh(source, matrix, &data)?;
            if !mesh.triangles.is_empty() {
                bodies.push(build(label, mesh)?);
            }
        }
    }
    if bodies.is_empty() {
        return Err(NO_FACES.into());
    }
    Ok(bodies)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn an_embedded_model_keeps_source_axes_and_uses_millimetres() {
        let positions: [[f32; 3]; 4] = [
            [0., 0., 0.],
            [0.03125, 0., 0.],
            [0., 0.015625, 0.],
            [0., 0., 0.0625],
        ];
        let mut bytes: Vec<u8> = positions
            .into_iter()
            .flatten()
            .flat_map(f32::to_le_bytes)
            .collect();
        let indices = [0_u16, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3];
        bytes.extend(indices.into_iter().flat_map(u16::to_le_bytes));
        let embedded = base64::engine::general_purpose::STANDARD.encode(&bytes);
        let manifest = json!({
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": bytes.len(), "uri": format!("data:application/octet-stream;base64,{embedded}")}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": 48, "target": 34962},
                {"buffer": 0, "byteOffset": 48, "byteLength": 24, "target": 34963}
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [0, 0, 0], "max": [0.03125, 0.015625, 0.0625]},
                {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"}
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
            "nodes": [{"translation": [0.125, 0, 0], "children": [1]}, {"mesh": 0, "translation": [0, 0.25, 0.5], "name": "Tetra"}],
            "scenes": [{"nodes": [0]}], "scene": 0
        });
        let bodies = read(&serde_json::to_vec(&manifest).unwrap(), "Imported").unwrap();
        assert_eq!(bodies.len(), 1);
        assert_eq!(bodies[0].name, "Tetra");
        assert!(bodies[0].note.is_empty());
        let bounds = occt::bounds(&bodies[0].shape).unwrap();
        for (actual, expected) in bounds
            .iter()
            .copied()
            .zip([125.0, 250.0, 500.0, 156.25, 265.625, 562.5])
        {
            assert!((actual - expected).abs() < 1e-6, "{bounds:?}");
        }
    }
}
