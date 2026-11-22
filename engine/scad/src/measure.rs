//! Measurements on the same OCCT shapes the modeling worker edits.

use occt::Shape;

pub struct Reading {
    pub label: &'static str,
    pub values: Vec<f64>,
    pub unit: &'static str,
}

/// What of a body is measured: all of it, or one face, edge or vertex (in `TopExp::MapShapes` order).
#[derive(Clone, Copy)]
pub enum Pick {
    Body,
    Face(u32),
    Edge(u32),
    Vertex(u32),
}

struct Selected<'a> {
    body: &'a Shape,
    pick: Pick,
    face: Option<occt::UniquePtr<Shape>>,
    index: Option<u32>,
    /// The picked edge or vertex.
    part: Option<occt::UniquePtr<Shape>>,
}

impl Selected<'_> {
    fn shape(&self) -> &Shape {
        self.face
            .as_deref()
            .or(self.part.as_deref())
            .unwrap_or(self.body)
    }

    fn center(&self) -> Result<Option<Vec<f64>>, occt::Exception> {
        match self.pick {
            Pick::Edge(index) => {
                let edge = &occt::edge_fingerprints(self.body)?[index as usize].curve;
                return Ok(Some(edge.center.into()));
            }
            Pick::Vertex(index) => {
                return Ok(Some(
                    occt::vertex_fingerprints(self.body)?[index as usize]
                        .center
                        .into(),
                ));
            }
            _ => {}
        }
        if let Some(face) = &self.face {
            let plane = occt::plane_frame(face)?;
            if plane.planar {
                return Ok(Some(plane.center.into()));
            }
            if let Some(index) = self.index
                && occt::face_fingerprint(self.body, index)?.geometry == "cylinder"
            {
                let cylinder = occt::cylinder_data(face)?;
                return Ok(Some(
                    (0..3)
                        .map(|axis| {
                            cylinder.origin[axis] + cylinder.direction[axis] * cylinder.length / 2.0
                        })
                        .collect(),
                ));
            }
            return Ok(None);
        }
        let center = occt::volume_center(self.body)?;
        Ok((center.len() == 3).then_some(center))
    }
}

fn reading(label: &'static str, value: f64, unit: &'static str) -> Reading {
    Reading {
        label,
        values: vec![value],
        unit,
    }
}

fn vector(label: &'static str, values: Vec<f64>, unit: &'static str) -> Reading {
    Reading {
        label,
        values,
        unit,
    }
}

fn extent(bounds: &[f64]) -> Vec<f64> {
    (0..3).map(|axis| bounds[axis + 3] - bounds[axis]).collect()
}

fn single(part: &Selected<'_>) -> Result<Vec<Reading>, occt::Exception> {
    let shape = part.shape();
    match part.pick {
        Pick::Edge(index) => {
            let edge = &occt::edge_fingerprints(part.body)?[index as usize].curve;
            let mut result = vec![reading("Length", edge.measure, "mm")];
            if edge.has_radius {
                result.push(reading("Radius", edge.radius, "mm"));
                result.push(reading("Diameter", edge.radius * 2.0, "mm"));
            }
            return Ok(result);
        }
        Pick::Vertex(_) => {
            return Ok(vec![vector(
                "Position",
                part.center()?.unwrap_or_default(),
                "mm",
            )]);
        }
        _ => {}
    }
    if let (Some(face), Some(index)) = (&part.face, part.index) {
        let data = occt::face_fingerprint(part.body, index)?;
        let area = reading("Area", occt::surface_area(shape)?, "mm²");
        if data.geometry == "cylinder" {
            let cylinder = occt::cylinder_data(face)?;
            return Ok(vec![
                reading("Diameter", cylinder.radius * 2.0, "mm"),
                reading("Radius", cylinder.radius, "mm"),
                reading("Length", cylinder.length, "mm"),
                vector("Axis", cylinder.direction.into(), ""),
                area,
            ]);
        }
        let plane = occt::plane_frame(face)?;
        let mut result = vec![area];
        if plane.planar {
            let mut span = extent(&occt::bounds(face)?);
            span.sort_by(|a, b| b.total_cmp(a));
            span.truncate(2);
            result.push(vector("Centre", plane.center.into(), "mm"));
            result.push(vector("Normal", plane.normal.into(), ""));
            result.push(vector("Extent", span, "mm"));
        }
        return Ok(result);
    }
    let mut result = vec![
        reading("Volume", occt::volume(shape).abs(), "mm³"),
        reading("Surface area", occt::surface_area(shape)?, "mm²"),
        vector("Bounding box", extent(&occt::bounds(shape)?), "mm"),
    ];
    if let Some(center) = part.center()? {
        result.push(vector("Centre of mass", center, "mm"));
    }
    Ok(result)
}

fn pair(first: &Selected<'_>, second: &Selected<'_>) -> Result<Vec<Reading>, occt::Exception> {
    let mut result = Vec::new();
    if let Ok(gap) = occt::minimum_distance(first.shape(), second.shape()) {
        result.push(reading(
            if gap > 1e-9 {
                "Minimum distance"
            } else {
                "Touching"
            },
            gap,
            "mm",
        ));
    }
    if let (Some(a), Some(b)) = (first.center()?, second.center()?) {
        let delta: Vec<f64> = a.iter().zip(b).map(|(a, b)| (a - b).abs()).collect();
        result.push(reading(
            "Centre distance",
            delta
                .iter()
                .map(|distance| distance * distance)
                .sum::<f64>()
                .sqrt(),
            "mm",
        ));
        for (label, distance) in ["ΔX", "ΔY", "ΔZ"].into_iter().zip(delta) {
            if distance > 1e-9 {
                result.push(reading(label, distance, "mm"));
            }
        }
    }
    if let (Some(a), Some(b)) = (&first.face, &second.face) {
        let (a, b) = (occt::plane_frame(a)?, occt::plane_frame(b)?);
        if a.planar && b.planar {
            let dot = a
                .normal
                .into_iter()
                .zip(b.normal)
                .map(|(a, b)| a * b)
                .sum::<f64>();
            result.push(reading(
                "Angle",
                dot.abs().min(1.0).acos().to_degrees(),
                "°",
            ));
        }
    }
    Ok(result)
}

pub fn measure(parts: &[(&Shape, Pick)]) -> Result<Vec<Reading>, occt::Exception> {
    let parts = parts
        .iter()
        .map(|&(body, pick)| {
            let index = match pick {
                Pick::Face(index) => Some(index),
                _ => None,
            };
            Ok(Selected {
                body,
                pick,
                index,
                face: index.map(|index| occt::sub_face(body, index)).transpose()?,
                part: match pick {
                    Pick::Edge(index) => Some(occt::sub_edge(body, index)?),
                    Pick::Vertex(index) => Some(occt::sub_vertex(body, index)?),
                    _ => None,
                },
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    match parts.as_slice() {
        [] => Ok(Vec::new()),
        [part] => single(part),
        [first, second] => pair(first, second),
        _ => {
            let mut overall = vec![f64::INFINITY; 3];
            overall.extend([f64::NEG_INFINITY; 3]);
            for part in &parts {
                let bounds = occt::bounds(part.shape())?;
                for axis in 0..3 {
                    overall[axis] = overall[axis].min(bounds[axis]);
                    overall[axis + 3] = overall[axis + 3].max(bounds[axis + 3]);
                }
            }
            Ok(vec![vector("Overall extent", extent(&overall), "mm")])
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn find<'a>(readings: &'a [Reading], label: &str) -> &'a [f64] {
        &readings.iter().find(|r| r.label == label).unwrap().values
    }

    #[test]
    fn edges_and_vertices_measure_alone_and_against_each_other() {
        let cube = occt::make_box(10.0, 20.0, 30.0).unwrap();
        let lengths: Vec<f64> = (0..12)
            .map(|edge| find(&measure(&[(&cube, Pick::Edge(edge))]).unwrap(), "Length")[0])
            .collect();
        for side in [10.0, 20.0, 30.0] {
            assert_eq!(
                lengths.iter().filter(|l| (*l - side).abs() < 1e-9).count(),
                4
            );
        }
        let rim = occt::make_cylinder(4.0, 7.0).unwrap();
        let circle = (0..occt::edge_fingerprints(&rim).unwrap().len() as u32)
            .find(|&edge| {
                occt::edge_fingerprints(&rim).unwrap()[edge as usize]
                    .curve
                    .has_radius
            })
            .unwrap();
        let readings = measure(&[(&rim, Pick::Edge(circle))]).unwrap();
        assert!((find(&readings, "Radius")[0] - 4.0).abs() < 1e-9);
        assert!((find(&readings, "Diameter")[0] - 8.0).abs() < 1e-9);

        let corners = occt::vertex_fingerprints(&cube).unwrap();
        let origin = corners.iter().position(|v| v.center == [0.0; 3]).unwrap() as u32;
        let far = corners
            .iter()
            .position(|v| v.center == [10.0, 20.0, 30.0])
            .unwrap() as u32;
        let position = measure(&[(&cube, Pick::Vertex(far))]).unwrap();
        assert_eq!(find(&position, "Position"), [10.0, 20.0, 30.0]);
        let apart = measure(&[(&cube, Pick::Vertex(origin)), (&cube, Pick::Vertex(far))]).unwrap();
        assert!((find(&apart, "Minimum distance")[0] - 1400f64.sqrt()).abs() < 1e-9);
    }
}
