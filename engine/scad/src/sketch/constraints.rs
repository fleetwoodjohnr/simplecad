use super::{Entity, Layout, Point, text};
use crate::evaluate::{Dimension, evaluate};
use crate::rebuild::{StepError, failed};
use serde_json::Value;
use std::collections::HashMap;
use std::f64::consts::{PI, TAU};

type Line = [Point; 2];
type Coordinates = [f64; 2];

pub(super) enum Constraint {
    Coincident(Point, Point),
    Horizontal(Line),
    Vertical(Line),
    Parallel(Line, Line),
    Perpendicular(Line, Line),
    EqualLength(Line, Line),
    EqualRadius(usize, usize),
    PointOnLine(Point, Line),
    Midpoint(Point, Line),
    Tangent(Line, Point, usize),
    Symmetric(Point, Point, Line),
    Fix(Point),
    Distance(Point, Point, f64),
    Diameter(usize, f64),
    Radius(usize, f64),
    Angle(Line, Line, f64),
}

pub(super) enum Residual {
    One(f64),
    Two(Coordinates),
}

impl Residual {
    pub(super) fn iter(&self) -> std::slice::Iter<'_, f64> {
        match self {
            Self::One(value) => std::slice::from_ref(value).iter(),
            Self::Two(values) => values.iter(),
        }
    }
}

impl Constraint {
    pub(super) fn read(
        row: &Value,
        layout: &Layout<'_>,
        parameters: &HashMap<String, f64>,
    ) -> Result<Self, StepError> {
        let kind = text(&row["kind"])?;
        let reference = |index| {
            text(&row["refs"][index])
                .map_err(|_| failed(format!("A {kind} constraint is missing a reference.")))
        };
        let point = |index| layout.point(reference(index)?);
        let entity = |index| layout.entity(reference(index)?);
        let line = |index| match entity(index)? {
            Entity::Line(points) => Ok(points),
            _ => Err(failed(format!("The {kind} constraint needs a line."))),
        };
        let round = |index| match entity(index)? {
            Entity::Round { centre, radius } => Ok((centre, radius)),
            _ => Err(failed(format!(
                "The {kind} constraint needs a circle or arc."
            ))),
        };
        let centre = |index| match entity(index)? {
            Entity::Round { centre, .. } | Entity::Ellipse { centre } => Ok(centre),
            _ => Err(failed(format!("The {kind} constraint needs a centre."))),
        };
        let dimension = |units| match row.get("expression").or_else(|| row.get("value")) {
            Some(Value::String(value)) => {
                evaluate(value, parameters, units).map_err(|err| failed(err.to_string()))
            }
            Some(value) => value
                .as_f64()
                .filter(|value| value.is_finite())
                .ok_or_else(|| failed(format!("The {kind} constraint needs a numeric value."))),
            None => Ok(0.0),
        };
        Ok(match kind {
            "coincident" => Self::Coincident(point(0)?, point(1)?),
            "horizontal" => Self::Horizontal(line(0)?),
            "vertical" => Self::Vertical(line(0)?),
            "parallel" => Self::Parallel(line(0)?, line(1)?),
            "perpendicular" => Self::Perpendicular(line(0)?, line(1)?),
            "equal" if matches!(entity(0)?, Entity::Line(_)) => {
                Self::EqualLength(line(0)?, line(1)?)
            }
            "equal" => Self::EqualRadius(round(0)?.1, round(1)?.1),
            "concentric" => Self::Coincident(centre(0)?, centre(1)?),
            "point_on_line" => Self::PointOnLine(point(0)?, line(1)?),
            "midpoint" => Self::Midpoint(point(0)?, line(1)?),
            "tangent" => {
                let (centre, radius) = round(1)?;
                Self::Tangent(line(0)?, centre, radius)
            }
            "symmetric" => Self::Symmetric(point(0)?, point(1)?, line(2)?),
            "fix" => Self::Fix(point(0)?),
            "distance" => Self::Distance(point(0)?, point(1)?, dimension(Dimension::Length)?),
            "radius" => Self::Radius(round(0)?.1, dimension(Dimension::Length)?),
            "diameter" => Self::Diameter(round(0)?.1, dimension(Dimension::Length)?),
            "angle" => Self::Angle(
                line(0)?,
                line(1)?,
                dimension(Dimension::Angle)?.to_radians(),
            ),
            _ => {
                return Err(failed(format!(
                    "Sketch constraint '{kind}' is unavailable."
                )));
            }
        })
    }

    pub(super) fn residuals(&self, values: &[f64]) -> Residual {
        use Residual::{One, Two};
        let point = |point: Point| [values[point.start], values[point.start + 1]];
        let direction = |[start, end]: Line| subtract(point(end), point(start));
        let line_distance = |target: Point, line: Line| {
            let dir = direction(line);
            cross(dir, subtract(point(target), point(line[0]))) / norm(dir).max(1e-9)
        };
        let relative = |a: Line, b: Line, compare: fn(Coordinates, Coordinates) -> f64| {
            let (a, b) = (direction(a), direction(b));
            10.0 * compare(a, b) / (norm(a) * norm(b)).max(1e-9)
        };
        match *self {
            Self::Coincident(a, b) => Two(subtract(point(a), point(b))),
            Self::Horizontal(line) => One(direction(line)[1]),
            Self::Vertical(line) => One(direction(line)[0]),
            Self::Parallel(a, b) => One(relative(a, b, cross)),
            Self::Perpendicular(a, b) => One(relative(a, b, dot)),
            Self::EqualLength(a, b) => One(norm(direction(a)) - norm(direction(b))),
            Self::EqualRadius(a, b) => One(values[a] - values[b]),
            Self::PointOnLine(target, line) => One(line_distance(target, line)),
            Self::Midpoint(p, [start, end]) => {
                Two(subtract(point(p), midpoint(point(start), point(end))))
            }
            Self::Tangent(line, centre, radius) => {
                One(line_distance(centre, line).abs() - values[radius])
            }
            Self::Symmetric(a, b, line) => {
                let (a, b) = (point(a), point(b));
                let axis = direction(line);
                let unit = axis.map(|v| v / norm(axis).max(1e-9));
                Two([
                    cross(unit, subtract(midpoint(a, b), point(line[0]))),
                    dot(unit, subtract(b, a)),
                ])
            }
            Self::Fix(p) => Two(p
                .fixed
                .map_or([0.0, 0.0], |target| subtract(point(p), target))),
            Self::Distance(a, b, length) => One(norm(subtract(point(b), point(a))) - length),
            Self::Radius(radius, target) => One(values[radius] - target),
            Self::Diameter(radius, target) => One(2.0 * values[radius] - target),
            Self::Angle(a, b, target) => {
                let (a, b) = (direction(a), direction(b));
                let current = cross(a, b).atan2(dot(a, b));
                One(((current - target + PI).rem_euclid(TAU) - PI) * 10.0)
            }
        }
    }
}

fn midpoint(a: Coordinates, b: Coordinates) -> Coordinates {
    [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0]
}

fn subtract(a: Coordinates, b: Coordinates) -> Coordinates {
    [a[0] - b[0], a[1] - b[1]]
}

fn dot(a: Coordinates, b: Coordinates) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}

fn cross(a: Coordinates, b: Coordinates) -> f64 {
    a[0] * b[1] - a[1] * b[0]
}

fn norm(a: Coordinates) -> f64 {
    a[0].hypot(a[1])
}
