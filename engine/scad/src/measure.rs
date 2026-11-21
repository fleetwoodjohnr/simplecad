//! Measurements on the same OCCT shapes the modeling worker edits.

use occt::Shape;

pub struct Reading {
    pub label: &'static str,
    pub values: Vec<f64>,
    pub unit: &'static str,
}

struct Selected<'a> {
    body: &'a Shape,
    face: Option<occt::UniquePtr<Shape>>,
    index: Option<u32>,
}

impl Selected<'_> {
    fn shape(&self) -> &Shape {
        self.face.as_deref().unwrap_or(self.body)
    }

    fn center(&self) -> Result<Option<Vec<f64>>, occt::Exception> {
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

pub fn measure(parts: &[(&Shape, Option<u32>)]) -> Result<Vec<Reading>, occt::Exception> {
    let parts = parts
        .iter()
        .map(|&(body, index)| {
            Ok(Selected {
                body,
                index,
                face: index.map(|index| occt::sub_face(body, index)).transpose()?,
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
