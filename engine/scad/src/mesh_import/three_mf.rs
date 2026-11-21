//! 3MF Core and Production extension geometry, following only package parts.

use super::{ImportedBody, Mesh, Point, Triangle, build};
use quick_xml::{
    Reader, XmlVersion,
    events::{BytesStart, Event},
};
use std::borrow::Cow;
use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::rc::Rc;
use std::str::FromStr;
use zip::ZipArchive;

const MODEL_RELATIONSHIP: &str = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel";
const MAX_PACKAGE_DATA: u64 = 256 * 1024 * 1024;
const MAX_COMPONENT_DEPTH: usize = 16;
const INVALID: &str = "The 3MF file contains invalid model data.";

#[derive(Clone, Copy)]
struct Matrix([f64; 12]);

impl Matrix {
    const IDENTITY: Self = Self([1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.]);

    fn read(node: &BytesStart<'_>) -> Result<Self, String> {
        let Some(encoded) = attribute(node, "transform") else {
            return Ok(Self::IDENTITY);
        };
        let values = encoded
            .split_whitespace()
            .map(str::parse::<f64>)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| INVALID)?;
        let values: [f64; 12] = values.try_into().map_err(|_| INVALID)?;
        if !values.iter().all(|entry| entry.is_finite()) {
            return Err(INVALID.into());
        }
        Ok(Self(values))
    }

    // 3MF uses row vectors, with translation in the last row.
    fn then(self, next: Self) -> Self {
        Self(std::array::from_fn(|index| {
            let (row, column) = (index / 3, index % 3);
            let transformed = (0..3)
                .map(|k| self.0[row * 3 + k] * next.0[k * 3 + column])
                .sum::<f64>();
            transformed + if row == 3 { next.0[index] } else { 0.0 }
        }))
    }

    fn point(self, point: Point, unit: f64) -> Point {
        std::array::from_fn(|column| {
            ((0..3)
                .map(|row| point[row] * self.0[row * 3 + column])
                .sum::<f64>()
                + self.0[9 + column])
                * unit
        })
    }
}

struct BuildItem {
    id: u32,
    part: Option<String>,
    name: Option<String>,
    transform: Matrix,
}

impl BuildItem {
    fn read(node: &BytesStart<'_>) -> Result<Self, String> {
        Ok(Self {
            id: required(node, "objectid")?,
            part: attribute(node, "path").map(Cow::into_owned),
            name: attribute(node, "name").map(Cow::into_owned),
            transform: Matrix::read(node)?,
        })
    }
}

struct Object {
    id: u32,
    name: Option<String>,
    is_support: bool,
    mesh: Mesh,
    components: Vec<BuildItem>,
}

struct Model {
    unit: f64,
    order: Vec<u32>,
    objects: HashMap<u32, Object>,
    build: Vec<BuildItem>,
}

fn attribute<'a>(node: &'a BytesStart<'_>, name: &str) -> Option<Cow<'a, str>> {
    node.attributes()
        .flatten()
        .find(|attr| attr.key.local_name().as_ref() == name)?
        .normalized_value(XmlVersion::Implicit1_0)
        .ok()
}

fn required<T: FromStr>(node: &BytesStart<'_>, name: &str) -> Result<T, String> {
    attribute(node, name)
        .and_then(|value| value.parse().ok())
        .ok_or_else(|| INVALID.into())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tag {
    Model,
    Resources,
    Object,
    Mesh,
    Vertices,
    Triangles,
    Components,
    Build,
    Other,
}

fn parse_model(bytes: &[u8]) -> Result<Model, String> {
    use Tag::*;
    let mut reader = Reader::from_reader(bytes);
    reader.config_mut().expand_empty_elements = true;
    let mut stack = Vec::new();
    let mut model = self::Model {
        unit: 1.0,
        order: Vec::new(),
        objects: HashMap::new(),
        build: Vec::new(),
    };
    let mut current: Option<self::Object> = None;
    let mut opened = false;
    loop {
        match reader.read_event().map_err(|_| INVALID)? {
            Event::Start(node) => {
                if stack.len() >= 256 {
                    return Err(INVALID.into());
                }
                let tag = match (stack.last().copied(), node.local_name().as_ref()) {
                    (None, "model") if !opened => {
                        opened = true;
                        model.unit = match attribute(&node, "unit")
                            .unwrap_or_default()
                            .to_ascii_lowercase()
                            .as_str()
                        {
                            "micron" => 0.001,
                            "centimeter" => 10.0,
                            "inch" => 25.4,
                            "foot" => 304.8,
                            "meter" => 1000.0,
                            _ => 1.0,
                        };
                        Model
                    }
                    (Some(Model), "resources") => Resources,
                    (Some(Model), "build") => Build,
                    (Some(Resources), "object") => {
                        current = Some(self::Object {
                            id: required(&node, "id")?,
                            name: attribute(&node, "name").map(Cow::into_owned),
                            is_support: matches!(
                                attribute(&node, "type").as_deref(),
                                Some("support" | "solidsupport" | "other")
                            ),
                            mesh: self::Mesh::default(),
                            components: Vec::new(),
                        });
                        Object
                    }
                    (Some(Object), "mesh") => Mesh,
                    (Some(Object), "components") => Components,
                    (Some(Mesh), "vertices") => Vertices,
                    (Some(Mesh), "triangles") => Triangles,
                    (Some(Vertices), "vertex") => {
                        let point: Point = [
                            required(&node, "x")?,
                            required(&node, "y")?,
                            required(&node, "z")?,
                        ];
                        if !point.iter().all(|value| value.is_finite()) {
                            return Err(INVALID.into());
                        }
                        current.as_mut().unwrap().mesh.points.push(point);
                        Other
                    }
                    (Some(Triangles), "triangle") => {
                        let triangle: Triangle = [
                            required(&node, "v1")?,
                            required(&node, "v2")?,
                            required(&node, "v3")?,
                        ];
                        current.as_mut().unwrap().mesh.triangles.push(triangle);
                        Other
                    }
                    (Some(Components), "component") => {
                        current
                            .as_mut()
                            .unwrap()
                            .components
                            .push(BuildItem::read(&node)?);
                        Other
                    }
                    (Some(Build), "item") => {
                        model.build.push(BuildItem::read(&node)?);
                        Other
                    }
                    _ => Other,
                };
                stack.push(tag);
            }
            Event::End(_) => {
                if stack.pop() == Some(Object) {
                    let object = current.take().unwrap();
                    if object
                        .mesh
                        .triangles
                        .iter()
                        .flatten()
                        .any(|&index| index as usize >= object.mesh.points.len())
                    {
                        return Err("A 3MF triangle references a missing vertex.".into());
                    }
                    model.order.push(object.id);
                    if model.objects.insert(object.id, object).is_some() {
                        return Err(INVALID.into());
                    }
                }
            }
            Event::DocType(_) => return Err(INVALID.into()),
            Event::Eof => break,
            _ => {}
        }
    }
    if !opened || !stack.is_empty() {
        return Err(INVALID.into());
    }
    Ok(model)
}

fn part_path(current: &str, target: &str) -> Result<String, String> {
    // Paths are package-relative URIs. This function never gives them to the OS.
    if target.contains([':', '\\', '?', '#']) {
        return Err(INVALID.into());
    }
    let base = if target.starts_with('/') {
        ""
    } else {
        current.rsplit_once('/').map_or("", |(folder, _)| folder)
    };
    let mut parts = Vec::new();
    for entry in base.split('/').chain(target.split('/')) {
        match entry {
            "" | "." => {}
            ".." => {
                parts.pop().ok_or(INVALID)?;
            }
            name => parts.push(name),
        }
    }
    Ok(parts.join("/"))
}

struct Package<'a> {
    archive: ZipArchive<Cursor<&'a [u8]>>,
    models: HashMap<String, Rc<Model>>,
    remaining: u64,
}

impl Package<'_> {
    fn part(&mut self, path: &str) -> Result<Vec<u8>, String> {
        let entry = self
            .archive
            .by_name(path)
            .map_err(|_| "The 3MF package is missing a model part.")?;
        if entry.size() > self.remaining {
            return Err("The 3MF package expands past the model size limit.".into());
        }
        let mut bytes = Vec::new();
        entry
            .take(self.remaining + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| INVALID)?;
        self.remaining = self
            .remaining
            .checked_sub(bytes.len() as u64)
            .ok_or("The 3MF package expands past the model size limit.")?;
        Ok(bytes)
    }

    fn root(&mut self) -> Result<String, String> {
        if self.archive.index_for_name("_rels/.rels").is_some() {
            let relationships = self.part("_rels/.rels")?;
            let mut reader = Reader::from_reader(relationships.as_slice());
            loop {
                match reader.read_event().map_err(|_| INVALID)? {
                    Event::Start(entry) | Event::Empty(entry)
                        if entry.local_name().as_ref() == "Relationship" =>
                    {
                        if attribute(&entry, "Type").as_deref() == Some(MODEL_RELATIONSHIP)
                            && attribute(&entry, "TargetMode").as_deref() != Some("External")
                            && let Some(target) = attribute(&entry, "Target")
                        {
                            let path = part_path("", &target)?;
                            if self.archive.index_for_name(&path).is_some() {
                                return Ok(path);
                            }
                        }
                    }
                    Event::DocType(_) => return Err(INVALID.into()),
                    Event::Eof => break,
                    _ => {}
                }
            }
        }
        if let Some(name) = ["3D/3dmodel.model", "3d/3dmodel.model"]
            .into_iter()
            .find(|name| self.archive.index_for_name(name).is_some())
        {
            return Ok(name.into());
        }
        self.archive
            .file_names()
            .filter_map(Result::ok)
            .find(|name| name.to_ascii_lowercase().ends_with(".model"))
            .map(Cow::into_owned)
            .ok_or_else(|| "The 3MF package has no model part.".into())
    }

    fn model(&mut self, path: &str) -> Result<Rc<Model>, String> {
        if let Some(model) = self.models.get(path) {
            return Ok(Rc::clone(model));
        }
        let model = Rc::new(parse_model(&self.part(path)?)?);
        self.models.insert(path.into(), Rc::clone(&model));
        Ok(model)
    }

    fn gather(
        &mut self,
        path: &str,
        item: &BuildItem,
        transform: Matrix,
        output: &mut Collected,
    ) -> Result<(), String> {
        let path = match &item.part {
            Some(part) => part_path(path, part)?,
            None => path.into(),
        };
        let key = (path, item.id);
        if output.chain.contains(&key) {
            return Err("This 3MF refers to itself and cannot be read.".into());
        }
        if output.chain.len() > MAX_COMPONENT_DEPTH {
            return Err("This 3MF nests model parts too deeply.".into());
        }
        let model = self.model(&key.0)?;
        let object = model
            .objects
            .get(&item.id)
            .ok_or("The 3MF build references a missing object.")?;
        if object.is_support {
            return Ok(());
        }
        let transform = item.transform.then(transform);
        output.append(&object.mesh, transform)?;
        output.chain.push(key);
        for component in &object.components {
            let own_part = output.chain.last().unwrap().0.clone();
            self.gather(&own_part, component, transform, output)?;
        }
        output.chain.pop();
        Ok(())
    }
}

struct Collected {
    unit: f64,
    mesh: Mesh,
    chain: Vec<(String, u32)>,
    available: u64,
}

impl Collected {
    fn append(&mut self, mesh: &Mesh, transform: Matrix) -> Result<(), String> {
        let size = (mesh.points.len() * size_of::<Point>()
            + mesh.triangles.len() * size_of::<Triangle>()) as u64;
        self.available = self
            .available
            .checked_sub(size)
            .ok_or("The assembled 3MF exceeds the model size limit.")?;
        let offset = self.mesh.points.len() as u32;
        for point in &mesh.points {
            let placed = transform.point(*point, self.unit);
            if !placed.iter().all(|value| value.is_finite()) {
                return Err(INVALID.into());
            }
            self.mesh.points.push(placed);
        }
        self.mesh.triangles.extend(
            mesh.triangles
                .iter()
                .map(|triangle| triangle.map(|index| index + offset)),
        );
        Ok(())
    }
}

fn slicer_names(package: &mut Package) -> Result<HashMap<u32, String>, String> {
    let mut names = HashMap::new();
    let path = "Metadata/model_settings.config";
    if package.archive.index_for_name(path).is_none() {
        return Ok(names);
    }
    let bytes = package.part(path)?;
    let mut reader = Reader::from_reader(bytes.as_slice());
    let mut id = None;
    loop {
        match reader.read_event() {
            Ok(Event::Start(node)) if node.local_name().as_ref() == "object" => {
                id = required(&node, "id").ok()
            }
            Ok(Event::Start(node) | Event::Empty(node))
                if node.local_name().as_ref() == "metadata" =>
            {
                if let Some(id) = id
                    && attribute(&node, "key").as_deref() == Some("name")
                    && let Some(name) = attribute(&node, "value").filter(|name| !name.is_empty())
                {
                    names.entry(id).or_insert_with(|| name.into_owned());
                }
            }
            Ok(Event::End(node)) if node.local_name().as_ref() == "object" => id = None,
            Ok(Event::Eof | Event::DocType(_)) | Err(_) => break,
            _ => {}
        }
    }
    Ok(names)
}

pub(super) fn read(bytes: &[u8], fallback: &str) -> Result<Vec<ImportedBody>, String> {
    let archive =
        ZipArchive::new(Cursor::new(bytes)).map_err(|_| "This is not a readable 3MF package.")?;
    let mut package = Package {
        archive,
        models: HashMap::new(),
        remaining: MAX_PACKAGE_DATA,
    };
    let path = package.root()?;
    let model = package.model(&path)?;
    let labels = slicer_names(&mut package)?;
    let default_items;
    let items = if model.build.is_empty() {
        default_items = model
            .order
            .iter()
            .map(|&id| BuildItem {
                id,
                part: None,
                name: None,
                transform: Matrix::IDENTITY,
            })
            .collect::<Vec<_>>();
        &default_items
    } else {
        &model.build
    };
    let mut names: HashMap<String, usize> = HashMap::new();
    let mut total_remaining = MAX_PACKAGE_DATA;
    let mut parts = Vec::new();
    for item in items {
        let mut output = Collected {
            unit: model.unit,
            mesh: Mesh::default(),
            chain: Vec::new(),
            available: total_remaining,
        };
        package.gather(&path, item, Matrix::IDENTITY, &mut output)?;
        total_remaining = output.available;
        if output.mesh.triangles.is_empty() {
            continue;
        }
        let name = [
            labels.get(&item.id),
            item.name.as_ref(),
            model
                .objects
                .get(&item.id)
                .and_then(|object| object.name.as_ref()),
        ]
        .into_iter()
        .flatten()
        .find(|name| !name.is_empty())
        .map_or(fallback, String::as_str);
        let occurrence = names.entry(name.into()).or_insert(0);
        *occurrence += 1;
        let name = if *occurrence == 1 {
            name.into()
        } else {
            format!("{name} {occurrence}")
        };
        parts.push(build(name, output.mesh)?);
    }
    if parts.is_empty() {
        return Err("The 3MF package has no usable mesh on the build plate.".into());
    }
    Ok(parts)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn archive(parts: &[(&str, &str)]) -> Vec<u8> {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for &(part, contents) in parts {
            zip.start_file(part, zip::write::SimpleFileOptions::default())
                .unwrap();
            zip.write_all(contents.as_bytes()).unwrap();
        }
        zip.finish().unwrap().into_inner()
    }

    #[test]
    fn package_relationships_and_component_transforms_use_build_plate_units() {
        let relationships = format!(
            r#"<Relationships><Relationship Type="{MODEL_RELATIONSHIP}" Target="/Plate/build.model"/></Relationships>"#
        );
        let root = r#"<model unit="inch" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
          <resources><object id="1"><components>
            <component objectid="2" p:path="../Parts/Shape.model" transform="1 0 0 0 1 0 0 0 1 2 0 0"/>
            <component objectid="3" p:path="../Parts/Shape.model"/>
          </components></object></resources>
          <build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 3 4 0"/></build>
        </model>"#;
        let leaf = r#"<model><resources><object id="2"><mesh>
          <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
            <vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/></vertices>
          <triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>
            <triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles>
        </mesh></object><object id="3" type="support"/></resources></model>"#;
        let slicer = r#"<config><object id="1"><metadata key="name" value="Tetrahedron &amp; base"/></object></config>"#;
        let package = archive(&[
            ("Parts/Shape.model", leaf),
            ("_rels/.rels", &relationships),
            ("Plate/build.model", root),
            ("Metadata/model_settings.config", slicer),
        ]);
        let mut bodies = read(&package, "Test").unwrap();
        assert_eq!(bodies.len(), 1);
        let body = bodies.remove(0);
        assert_eq!(body.name, "Tetrahedron & base");
        assert_eq!(occt::solid_count(&body.shape), 1);
        assert!((occt::volume(&body.shape) - 25.4_f64.powi(3) / 6.0).abs() < 1e-6);
        let bounds = occt::bounds(&body.shape).unwrap();
        assert!((bounds[0] - 127.0).abs() < 1e-6);
        assert!((bounds[1] - 101.6).abs() < 1e-6);
    }

    #[test]
    fn rejects_circular_components_and_external_parts() {
        let root = r#"<model><resources><object id="1"><components><component objectid="1"/>
           </components></object></resources><build><item objectid="1"/></build></model>"#;
        let package = archive(&[("3D/3dmodel.model", root)]);
        assert!(
            read(&package, "Cycle")
                .err()
                .unwrap()
                .contains("refers to itself")
        );
        assert!(part_path("3D/3dmodel.model", "https://example.invalid/model").is_err());
        assert!(part_path("3D/3dmodel.model", "../../outside.model").is_err());
    }
}
