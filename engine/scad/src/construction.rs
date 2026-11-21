//! Work geometry stored as the same non-solid results as v0.4.4.
use crate::align::format_g;
use crate::evaluate::Dimension::{Angle, Length};
use crate::rebuild::{Context, Shape, StepError, failed, kernel};
use crate::vector::{V, add, cross, length, perpendicular, scale, sub, unit};
use serde_json::{Value, json};

pub(crate) enum Kind {
    OffsetPlane,
    Midplane,
    PlaneAtAngle,
    PlaneThroughPoints,
    TangentPlane,
    Axis,
    Point,
}

impl Kind {
    pub(crate) fn from_feature(kind: &str) -> Option<Self> {
        Some(match kind {
            "offset_plane" => Self::OffsetPlane,
            "midplane" => Self::Midplane,
            "plane_at_angle" => Self::PlaneAtAngle,
            "plane_through_points" => Self::PlaneThroughPoints,
            "tangent_plane" => Self::TangentPlane,
            "construction_axis" => Self::Axis,
            "construction_point" => Self::Point,
            _ => return None,
        })
    }

    pub(crate) fn cache_kind(&self) -> &'static str {
        match self {
            Self::Axis => "ConstructionAxis",
            Self::Point => "ConstructionPoint",
            _ => "ConstructionPlane",
        }
    }

    pub(crate) fn execute(&self, ctx: &Context, name: &str) -> Result<Value, StepError> {
        let plane = |origin: V, normal: V, x_axis: V| json!({ "name": name, "origin": origin, "normal": normal, "x_axis": x_axis });
        let data = match self {
            Self::OffsetPlane => {
                let face = planar(
                    ctx.face("face")?,
                    "An offset plane needs a flat face to offset from.",
                )?;
                let distance = ctx.value("distance", 10.0, Length)?;
                ctx.set_message(format!("{} mm from the face", format_g(distance)));
                plane(
                    add(face.center, scale(face.normal, distance)),
                    face.normal,
                    perpendicular(face.normal)?,
                )
            }
            Self::Midplane => {
                let faces = ctx.faces("faces")?;
                let [first, second] =
                    <[_; 2]>::try_from(faces).map_err(|_| failed("A midplane needs two faces."))?;
                let message = "A midplane needs two flat faces.";
                let first = planar(first, message)?;
                let second = planar(second, message)?;
                let gap = length(sub(first.center, second.center));
                let normal = unit(first.normal)?;
                ctx.set_message(format!("halfway across {gap:.2} mm"));
                plane(
                    scale(add(first.center, second.center), 0.5),
                    normal,
                    perpendicular(normal)?,
                )
            }
            Self::PlaneAtAngle => {
                let face = planar(
                    ctx.face("face")?,
                    "A plane at an angle needs a flat face to start from.",
                )?;
                let edge = ctx.edge_axis("edge")?;
                let angle = ctx.value("angle", 45.0, Angle)?;
                let rotation = occt::trsf_rotation(&[0.0; 3], &edge.direction, angle.to_radians())
                    .map_err(kernel)?;
                let normal = occt::trsf_point(&rotation, &face.normal);
                let normal = [normal[0], normal[1], normal[2]];
                ctx.set_message(format!("{}° from the face", format_g(angle)));
                plane(edge.origin, normal, perpendicular(normal)?)
            }
            Self::PlaneThroughPoints => {
                let points = match ctx
                    .input("points")
                    .and_then(Value::as_array)
                    .filter(|points| !points.is_empty())
                {
                    Some(points) => points
                        .iter()
                        .take(3)
                        .map(coordinates)
                        .collect::<Result<_, _>>()?,
                    None => ctx.vertices("vertices")?,
                };
                let [a, b, c, ..] = points.as_slice() else {
                    return Err(failed("A plane needs three points."));
                };
                let along = sub(*b, *a);
                let normal = cross(along, sub(*c, *a));
                if length(normal) < 1e-9 {
                    return Err(failed("Those three points are in a line."));
                }
                plane(*a, unit(normal)?, unit(along)?)
            }
            Self::TangentPlane => {
                let cylinder = cylinder(ctx.face("face")?)?;
                if !cylinder.cylindrical {
                    return Err(failed("A tangent plane needs a round face."));
                }
                let (sine, cosine) = ctx.value("around", 0.0, Angle)?.to_radians().sin_cos();
                let axis = unit(cylinder.direction)?;
                let reference = unit(cross(axis, perpendicular(axis)?))?;
                let second = unit(cross(axis, reference))?;
                let outward = add(scale(reference, cosine), scale(second, sine));
                ctx.set_message(format!("tangent to ⌀{:.2}", cylinder.radius * 2.0));
                plane(
                    add(
                        cylinder_center(&cylinder, axis),
                        scale(outward, cylinder.radius),
                    ),
                    outward,
                    axis,
                )
            }
            Self::Axis => {
                let (origin, direction) = if present(ctx, "face") {
                    let cylinder = cylinder(ctx.face("face")?)?;
                    if !cylinder.cylindrical {
                        return Err(failed("That face has no axis. Select a round face."));
                    }
                    ctx.set_message(format!("axis of ⌀{:.2}", cylinder.radius * 2.0));
                    (cylinder.origin, unit(cylinder.direction)?)
                } else if present(ctx, "edge") {
                    let edge = ctx.edge_axis("edge")?;
                    (edge.origin, edge.direction)
                } else {
                    return Err(failed("An axis needs a round face or a straight edge."));
                };
                json!({ "name": name, "origin": origin, "direction": direction })
            }
            Self::Point => {
                let position = if present(ctx, "face") {
                    let source = ctx.face("face")?;
                    let cylinder = cylinder(source.clone())?;
                    if cylinder.cylindrical {
                        cylinder_center(&cylinder, unit(cylinder.direction)?)
                    } else {
                        planar(source, "That face has no centre to mark.")?.center
                    }
                } else {
                    [
                        ctx.value("x", 0.0, Length)?,
                        ctx.value("y", 0.0, Length)?,
                        ctx.value("z", 0.0, Length)?,
                    ]
                };
                let [x, y, z] = position;
                ctx.set_message(format!("{x:.2}, {y:.2}, {z:.2}"));
                json!({ "name": name, "position": position })
            }
        };
        Ok(json!({ "kind": self.cache_kind(), "data": data }))
    }
}

fn present(ctx: &Context, key: &str) -> bool {
    ctx.input(key).is_some_and(|value| !value.is_null())
}

fn planar((shape, index): (Shape, u32), message: &str) -> Result<occt::PlaneFrame, StepError> {
    let face = occt::sub_face(&shape, index).map_err(kernel)?;
    let plane = occt::plane_frame(&face).map_err(kernel)?;
    plane.planar.then_some(plane).ok_or_else(|| failed(message))
}

fn cylinder((shape, index): (Shape, u32)) -> Result<occt::CylinderData, StepError> {
    let face = occt::sub_face(&shape, index).map_err(kernel)?;
    occt::cylinder_data(&face).map_err(kernel)
}

fn cylinder_center(cylinder: &occt::CylinderData, axis: V) -> V {
    add(cylinder.origin, scale(axis, cylinder.length / 2.0))
}

fn coordinates(value: &Value) -> Result<V, StepError> {
    let parsed = || {
        let [x, y, z] = value.as_array()?.as_slice() else {
            return None;
        };
        Some([x.as_f64()?, y.as_f64()?, z.as_f64()?])
    };
    parsed().ok_or_else(|| failed("A construction point needs three coordinates."))
}
