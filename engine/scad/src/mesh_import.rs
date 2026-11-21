//! STL and OBJ inputs and their shared indexed-mesh reconstruction.

use occt::ImportedBody;
use std::collections::{BTreeSet, HashMap};

mod three_mf;

const REBUILD_TRIANGLE_LIMIT: usize = 40_000;
const SLIVER_REPAIR_WORK_LIMIT: usize = 2_000_000;
const NO_FACES: &str = "The file contains no usable mesh faces.";

type Point = [f64; 3];
type Triangle = [u32; 3];

#[derive(Default)]
struct Mesh {
    points: Vec<Point>,
    triangles: Vec<Triangle>,
}

/// Read only the provided bytes. OBJ material assignments and other external
/// file references have no role in constructing the geometry.
pub fn read(format: &str, bytes: &[u8], name: &str) -> Result<Vec<ImportedBody>, String> {
    match format {
        "stl" => Ok(vec![build(name.into(), read_stl(bytes)?)?]),
        "obj" => read_obj(bytes, name),
        "3mf" => three_mf::read(bytes, name),
        _ => Err("Choose an STL, OBJ or 3MF file.".into()),
    }
}

fn read_stl(bytes: &[u8]) -> Result<Mesh, String> {
    let mut mesh = Mesh::default();
    let mut seen: HashMap<[u64; 3], u32> = HashMap::new();
    let mut facet = |points: [Point; 3]| -> Result<(), String> {
        let mut triangle = [0; 3];
        for (index, point) in triangle.iter_mut().zip(points) {
            if !point.iter().all(|v| v.is_finite()) {
                return Err("The STL contains a non-finite vertex.".into());
            }
            let key = point.map(|value| if value == 0.0 { 0 } else { value.to_bits() });
            *index = *seen.entry(key).or_insert_with(|| {
                let index = mesh.points.len() as u32;
                mesh.points.push(point);
                index
            });
        }
        mesh.triangles.push(triangle);
        Ok(())
    };
    let defined = bytes
        .get(80..84)
        .map(|count| u32::from_le_bytes(count.try_into().unwrap()) as usize);
    if defined.and_then(|count| count.checked_mul(50)) == bytes.len().checked_sub(84)
        && defined.is_some()
    {
        let (records, _) = bytes[84..].as_chunks::<50>();
        for record in records {
            let mut points = [[0.0; 3]; 3];
            for (value, bytes) in points
                .iter_mut()
                .flatten()
                .zip(record[12..48].as_chunks::<4>().0)
            {
                *value = f64::from(f32::from_le_bytes(*bytes));
            }
            facet(points)?;
        }
        return Ok(mesh);
    }
    let text = std::str::from_utf8(bytes).map_err(|_| "This is not a readable STL file.")?;
    if !text.trim_start().starts_with("solid") {
        return Err("This is not a readable STL file.".into());
    }
    let mut points = [[0.0; 3]; 3];
    let mut vertices = 0;
    for line in text.lines() {
        let mut words = line.split_whitespace();
        match words.next() {
            Some("vertex") if vertices < 3 => {
                points[vertices] = point(words)?;
                vertices += 1;
            }
            Some("endfacet") if vertices == 3 => {
                facet(points)?;
                vertices = 0;
            }
            Some("vertex" | "endfacet") => {
                return Err("An STL facet must have three vertices.".into());
            }
            _ => {}
        }
    }
    if vertices != 0 {
        return Err("The STL ends inside a facet.".into());
    }
    Ok(mesh)
}

fn point<'a>(mut values: impl Iterator<Item = &'a str>) -> Result<Point, String> {
    let mut point = [0.0_f64; 3];
    for coordinate in &mut point {
        *coordinate = values
            .next()
            .and_then(|value| value.parse().ok())
            .filter(|value: &f64| value.is_finite())
            .ok_or("A mesh vertex needs three finite coordinates.")?;
    }
    Ok(point)
}

fn read_obj(bytes: &[u8], fallback: &str) -> Result<Vec<ImportedBody>, String> {
    let mut points = Vec::new();
    let mut bodies = Vec::new();
    let mut groups = Vec::new();
    let mut name = fallback.to_owned();
    let mut triangles = Vec::new();
    for line in String::from_utf8_lossy(bytes).lines() {
        let mut words = line.split('#').next().unwrap_or("").split_whitespace();
        match words.next() {
            Some("v") => points.push(point(words)?),
            Some("o" | "g") => {
                if !triangles.is_empty() {
                    groups.push((name, std::mem::take(&mut triangles)));
                }
                name = words.collect::<Vec<_>>().join(" ");
                if name.is_empty() {
                    name = fallback.into();
                }
            }
            Some("f") => {
                let indices = words
                    .map(|token| {
                        let raw = token
                            .split('/')
                            .next()
                            .unwrap_or("")
                            .parse::<i64>()
                            .map_err(|_| "An OBJ face has an invalid vertex index.")?;
                        let index = match raw {
                            0 => return Err("An OBJ face has an invalid vertex index."),
                            n if n < 0 => points.len() as i64 + n,
                            n => n - 1,
                        };
                        u32::try_from(index).map_err(|_| "An OBJ face has an invalid vertex index.")
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                if let Some(&first) = indices.first() {
                    triangles.extend(
                        indices[1..]
                            .windows(2)
                            .map(|edge| [first, edge[0], edge[1]]),
                    );
                }
            }
            _ => {}
        }
    }
    if !triangles.is_empty() {
        groups.push((name, triangles));
    }
    for (name, triangles) in groups {
        let mut mesh = Mesh::default();
        let mut local = HashMap::new();
        for mut triangle in triangles {
            for index in &mut triangle {
                let position = points
                    .get(*index as usize)
                    .ok_or("An OBJ face references a missing vertex.")?;
                *index = *local.entry(*index).or_insert_with(|| {
                    let local = mesh.points.len() as u32;
                    mesh.points.push(*position);
                    local
                });
            }
            mesh.triangles.push(triangle);
        }
        bodies.push(build(name, mesh)?);
    }
    if bodies.is_empty() {
        return Err(NO_FACES.into());
    }
    Ok(bodies)
}

fn delta(a: Point, b: Point) -> Point {
    std::array::from_fn(|i| a[i] - b[i])
}

fn length(vector: Point) -> f64 {
    vector[0].hypot(vector[1]).hypot(vector[2])
}

fn area_normal(a: Point, b: Point) -> Point {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

// A zero-area facet can leave a vertex on the side of a neighbouring facet.
// Split only unpaired edges and cap the search, as the legacy importer does.
fn remove_slivers(mesh: &mut Mesh) {
    let mut suspects = BTreeSet::new();
    mesh.triangles.retain(|&[a, b, c]| {
        let [a0, b0, c0] = [a, b, c].map(|i| mesh.points[i as usize]);
        let valid = length(area_normal(delta(b0, a0), delta(c0, a0))) >= 1e-12;
        if !valid {
            suspects.extend([a, b, c]);
        }
        valid
    });
    if suspects.is_empty() || mesh.triangles.is_empty() {
        return;
    }
    let edges = |[a, b, c]: Triangle| [(a, b), (b, c), (c, a)];
    let key = |a: u32, b: u32| (a.min(b), a.max(b));
    let mut uses = HashMap::new();
    for triangle in &mesh.triangles {
        for (a, b) in edges(*triangle) {
            *uses.entry(key(a, b)).or_insert(0) += 1;
        }
    }
    uses.retain(|_, count| *count != 2);
    if uses.is_empty() || uses.len().saturating_mul(suspects.len()) > SLIVER_REPAIR_WORK_LIMIT {
        return;
    }
    let mut output = Vec::new();
    for triangle in mesh.triangles.drain(..) {
        let mut boundary = Vec::new();
        for (a, b) in edges(triangle) {
            boundary.push(a);
            if !uses.contains_key(&key(a, b)) {
                continue;
            }
            let [p, q] = [a, b].map(|index| mesh.points[index as usize]);
            let span = length(delta(p, q));
            let mut inside = Vec::new();
            for &candidate in &suspects {
                if candidate == a || candidate == b {
                    continue;
                }
                let corner = mesh.points[candidate as usize];
                let (first, second) = (length(delta(p, corner)), length(delta(q, corner)));
                if first > 1e-9
                    && second > 1e-9
                    && (first + second - span).abs() <= 1e-7 * span.max(1.0)
                {
                    inside.push((first, candidate));
                }
            }
            inside.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
            boundary.extend(inside.into_iter().map(|(_, vertex)| vertex));
        }
        if boundary.len() == 3 {
            output.push(triangle);
            continue;
        }
        let centre = mesh.points.len() as u32;
        mesh.points.push(std::array::from_fn(|i| {
            triangle
                .iter()
                .map(|&v| mesh.points[v as usize][i])
                .sum::<f64>()
                / 3.0
        }));
        for index in 0..boundary.len() {
            output.push([
                centre,
                boundary[index],
                boundary[(index + 1) % boundary.len()],
            ]);
        }
    }
    mesh.triangles = output;
}

fn root(parent: &mut [u32], mut point: u32) -> u32 {
    while parent[point as usize] != point {
        parent[point as usize] = parent[parent[point as usize] as usize];
        point = parent[point as usize];
    }
    point
}

fn components(mesh: &Mesh) -> (Vec<u32>, Vec<u32>) {
    let mut parent: Vec<_> = (0..mesh.points.len() as u32).collect();
    for &[a, b, c] in &mesh.triangles {
        let head = root(&mut parent, a);
        for point in [b, c] {
            let joined = root(&mut parent, point);
            parent[joined as usize] = head;
        }
    }
    // Keep components in the order their first triangles appeared.
    let mut order = HashMap::new();
    let mut parts: Vec<Vec<u32>> = Vec::new();
    for triangle in &mesh.triangles {
        let index = *order
            .entry(root(&mut parent, triangle[0]))
            .or_insert_with(|| {
                parts.push(Vec::new());
                parts.len() - 1
            });
        parts[index].extend(triangle);
    }
    let mut ends = vec![0];
    let mut indices = Vec::with_capacity(mesh.triangles.len() * 3);
    for part in parts {
        indices.extend(part);
        ends.push(indices.len() as u32);
    }
    (indices, ends)
}

fn build(name: String, mut mesh: Mesh) -> Result<ImportedBody, String> {
    let triangle_count = mesh.triangles.len();
    let display_only = triangle_count > REBUILD_TRIANGLE_LIMIT;
    let (indices, boundaries) = if display_only {
        (
            mesh.triangles.iter().flatten().copied().collect(),
            Vec::new(),
        )
    } else {
        remove_slivers(&mut mesh);
        components(&mesh)
    };
    if indices.is_empty() {
        return Err(NO_FACES.into());
    }
    let built = occt::import_mesh(mesh.points.as_flattened(), &indices, &boundaries)
        .map_err(|error| error.what().to_string())?;
    let note = if display_only {
        format!(
            "This mesh has {triangle_count} triangles and is kept for display. Cutting requires a solid."
        )
    } else if built.open_components != 0 {
        format!(
            "{} part(s) of this mesh do not close and remain surfaces.",
            built.open_components
        )
    } else {
        String::new()
    };
    Ok(ImportedBody {
        name,
        shape: built.shape,
        note,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn binary_stl_recovers_six_editable_faces_from_a_box() {
        let cube = occt::make_box(10., 12., 13.).unwrap();
        let mesh = occt::mesh(&cube, 0.05, 0.35).unwrap();
        let bytes = crate::export::encode(
            "stl",
            &[crate::export::Part {
                name: "Box",
                positions: &mesh.positions,
                indices: &mesh.indices,
            }],
        )
        .unwrap();
        let solid = read("stl", &bytes, "Box").unwrap().remove(0);
        assert!(solid.note.is_empty(), "{}", solid.note);
        assert_eq!(occt::faces(&solid.shape).unwrap().len(), 6);
        assert!((occt::volume(&solid.shape) - 1560.0).abs() < 1e-6);
        assert!(occt::is_valid(&solid.shape).unwrap());
    }

    #[test]
    fn ascii_stl_open_surfaces_are_reported_and_invalid_values_are_rejected() {
        let mesh = b"solid panel\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 2 0 0\nvertex 0 3 0\nendloop\nendfacet\nendsolid panel\n";
        let panel = read("stl", mesh, "Panel").unwrap().remove(0);
        assert_eq!(occt::solid_count(&panel.shape), 0);
        assert!(panel.note.contains("remain surfaces"));
        let invalid = std::str::from_utf8(mesh)
            .unwrap()
            .replace("vertex 2 ", "vertex NaN ");
        assert!(read("stl", invalid.as_bytes(), "Panel").is_err());
    }

    #[test]
    fn obj_keeps_named_groups_and_resolves_negative_vertex_indices() {
        let text = br#"
v 0 0 0
v 1 0 0
v 0 1 0
v 0 0 1
v 2 0 0
v 3 0 0
v 2 1 0
v 2 0 1
o First
f 1 3 2
f 1 2 4
f 1 4 3
f 2 3 4
g Second
f -4/1 -2/1 -3/1
f -4 -3 -1
f -4 -1 -2
f -3 -2 -1
"#;
        let models = read("obj", text, "Test").unwrap();
        assert_eq!(
            models
                .iter()
                .map(|body| body.name.as_str())
                .collect::<Vec<_>>(),
            ["First", "Second"]
        );
        for model in models {
            assert!(model.note.is_empty());
            assert_eq!(occt::solid_count(&model.shape), 1);
            assert!((occt::volume(&model.shape) - 1.0 / 6.0).abs() < 1e-6);
        }
        assert!(read("obj", b"v 0 0 0\nf 1 2 3\n", "Invalid").is_err());
    }

    #[test]
    fn a_large_mesh_keeps_its_triangles_through_placement_and_brep() {
        let shape = build(
            "Large".into(),
            Mesh {
                points: vec![[0., 0., 0.], [2., 0., 0.], [0., 3., 0.]],
                triangles: vec![[0, 1, 2]; REBUILD_TRIANGLE_LIMIT + 1],
            },
        )
        .unwrap();
        assert!(shape.note.contains("40001 triangles"));
        assert!(
            occt::faces(&shape.shape)
                .unwrap()
                .iter()
                .all(|face| !face.planar)
        );
        let moved =
            occt::transform_shape(&shape.shape, &occt::trsf_translation(&[10., 0., 0.]), true)
                .unwrap();
        let reopened = occt::read_brep(&occt::write_brep(&moved).unwrap()).unwrap();
        let mesh = occt::mesh(&reopened, 0.05, 0.35).unwrap();
        assert_eq!(mesh.indices.len(), (REBUILD_TRIANGLE_LIMIT + 1) * 3);
        assert_eq!(mesh.positions[0], 10.0);
    }
}
