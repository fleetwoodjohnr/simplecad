//! Mesh files for the desktop export and print workflows.

use std::collections::HashMap;
use std::io::{self, Cursor, Write};

pub struct Part<'a> {
    pub name: &'a str,
    pub positions: &'a [f32],
    pub indices: &'a [u32],
}

struct Mesh<'a> {
    name: &'a str,
    vertices: Vec<[f32; 3]>,
    triangles: Vec<[u32; 3]>,
}

impl<'a> Mesh<'a> {
    fn from_part(part: &Part<'a>) -> Result<Self, String> {
        if !part.positions.len().is_multiple_of(3) || !part.indices.len().is_multiple_of(3) {
            return Err("The export mesh is incomplete.".into());
        }
        let mut vertices = Vec::new();
        let mut shared = HashMap::new();
        let mut remap = Vec::with_capacity(part.positions.len() / 3);
        for &point in part.positions.as_chunks::<3>().0 {
            if !point.iter().all(|axis| axis.is_finite()) {
                return Err("The export mesh has an invalid coordinate.".into());
            }
            // The display mesh splits vertices at face boundaries. Rejoin
            // coincident positions so the slicer sees the shared edges.
            let key = point.map(|axis| if axis == 0.0 { 0 } else { axis.to_bits() });
            let index = shared.entry(key).or_insert_with(|| {
                let index = vertices.len() as u32;
                vertices.push(point);
                index
            });
            remap.push(*index);
        }
        let vertex = |index: u32| {
            remap
                .get(index as usize)
                .copied()
                .ok_or_else(|| "The export mesh has an invalid vertex index.".to_string())
        };
        let mut triangles = Vec::with_capacity(part.indices.len() / 3);
        for face in part.indices.as_chunks::<3>().0 {
            let [a, b, c] = [vertex(face[0])?, vertex(face[1])?, vertex(face[2])?];
            if a != b && b != c && c != a {
                triangles.push([a, b, c]);
            }
        }
        Ok(Self {
            name: part.name,
            vertices,
            triangles,
        })
    }
}

pub fn encode(format: &str, parts: &[Part<'_>]) -> Result<Vec<u8>, String> {
    let meshes = parts
        .iter()
        .map(Mesh::from_part)
        .collect::<Result<Vec<_>, _>>()?;
    let meshes: Vec<_> = meshes
        .into_iter()
        .filter(|mesh| !mesh.triangles.is_empty())
        .collect();
    if meshes.is_empty() {
        return Err("There is no surface to export.".into());
    }
    match format {
        "stl" => stl(&meshes),
        "obj" => obj(&meshes),
        "3mf" => three_mf(&meshes),
        _ => return Err("Choose STL, OBJ or 3MF.".into()),
    }
    .map_err(|error| format!("The export could not be written: {error}"))
}

fn obj(meshes: &[Mesh<'_>]) -> io::Result<Vec<u8>> {
    let mut out = b"# SimpleCAD\n".to_vec();
    let mut offset = 1_u64;
    for mesh in meshes {
        let name = mesh.name.replace(['\r', '\n'], " ");
        writeln!(out, "o {name}")?;
        for [x, y, z] in &mesh.vertices {
            writeln!(out, "v {x:.6} {y:.6} {z:.6}")?;
        }
        for face in &mesh.triangles {
            let [a, b, c] = face.map(|index| offset + u64::from(index));
            writeln!(out, "f {a} {b} {c}")?;
        }
        offset += mesh.vertices.len() as u64;
    }
    Ok(out)
}

fn stl(meshes: &[Mesh<'_>]) -> io::Result<Vec<u8>> {
    let count = u32::try_from(
        meshes
            .iter()
            .map(|mesh| mesh.triangles.len())
            .sum::<usize>(),
    )
    .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Too many STL triangles."))?;
    let mut out = vec![0; 80];
    out[..9].copy_from_slice(b"SimpleCAD");
    out.extend(count.to_le_bytes());
    for mesh in meshes {
        for face in &mesh.triangles {
            let points = face.map(|index| mesh.vertices[index as usize]);
            let [a, b, c] = points.map(|point| point.map(f64::from));
            let [u, v] = [b, c].map(|p| std::array::from_fn::<_, 3, _>(|i| p[i] - a[i]));
            let axis = [
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            ];
            let length = axis.iter().map(|value| value * value).sum::<f64>().sqrt();
            let normal = axis.map(|value| {
                if length > 0.0 {
                    (value / length) as f32
                } else {
                    0.0
                }
            });
            for value in normal.into_iter().chain(points.into_iter().flatten()) {
                out.extend(value.to_le_bytes());
            }
            out.extend([0, 0]);
        }
    }
    Ok(out)
}

fn three_mf(meshes: &[Mesh<'_>]) -> io::Result<Vec<u8>> {
    let mut archive = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    archive.start_file("[Content_Types].xml", options)?;
    archive.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>"#)?;
    archive.start_file("_rels/.rels", options)?;
    archive.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>"#)?;
    archive.start_file("3D/3dmodel.model", options)?;
    archive.write_all(br#"<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>"#)?;
    for (index, mesh) in meshes.iter().enumerate() {
        let name = mesh
            .name
            .chars()
            .filter(|c| !c.is_control())
            .collect::<String>()
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('"', "&quot;");
        write!(
            archive,
            r#"<object id="{}" type="model" name="{name}"><mesh><vertices>"#,
            index + 1
        )?;
        for [x, y, z] in &mesh.vertices {
            write!(archive, r#"<vertex x="{x:.6}" y="{y:.6}" z="{z:.6}"/>"#)?;
        }
        archive.write_all(b"</vertices><triangles>")?;
        for [a, b, c] in &mesh.triangles {
            write!(archive, r#"<triangle v1="{a}" v2="{b}" v3="{c}"/>"#)?;
        }
        archive.write_all(b"</triangles></mesh></object>")?;
    }
    archive.write_all(b"</resources><build>")?;
    for id in 1..=meshes.len() {
        write!(archive, r#"<item objectid="{id}"/>"#)?;
    }
    archive.write_all(b"</build></model>")?;
    Ok(archive.finish()?.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn two_meshes_export_with_their_names_and_shared_vertices() {
        let positions = [0., 0., 0., 1., 0., 0., 0., 1., 0., -0., 0., 0.];
        let parts = ["A & B", "Second"].map(|name| Part {
            name,
            positions: &positions,
            indices: &[3, 1, 2],
        });
        let stl = encode("stl", &parts).unwrap();
        assert_eq!(stl.len(), 84 + 2 * 50);
        assert_eq!(&stl[80..84], &2_u32.to_le_bytes());
        let obj = String::from_utf8(encode("obj", &parts).unwrap()).unwrap();
        assert!(obj.contains("o A & B"));
        assert!(obj.contains("f 1 2 3\n"));
        assert!(obj.contains("f 4 5 6\n"));
        let mut archive =
            zip::ZipArchive::new(Cursor::new(encode("3mf", &parts).unwrap())).unwrap();
        let mut model = String::new();
        archive
            .by_name("3D/3dmodel.model")
            .unwrap()
            .read_to_string(&mut model)
            .unwrap();
        assert_eq!(archive.len(), 3);
        assert_eq!(model.matches("<vertex ").count(), 6);
        assert!(model.contains("name=\"A &amp; B\""));
        assert!(model.contains("<item objectid=\"2\"/>"));
    }
}
