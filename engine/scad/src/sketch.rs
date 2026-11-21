//! The v0.4 sketch recipe stores geometry in plane coordinates, keyed by stable IDs.
//! Compile it once to a parameter vector and evaluate constraints on the geometry worker.

mod constraints;
pub(crate) mod modeling;

use crate::rebuild::{StepError, failed};
use nalgebra::{DMatrix, DVector, linalg::SVD};
use serde_json::{Value, json};
use std::collections::HashMap;

use constraints::Constraint;

const SOLVE_TOLERANCE: f64 = 1e-5;
const ITERATION_TOLERANCE: f64 = 1e-10;

#[derive(Debug, PartialEq, Eq)]
pub enum State {
    Unconstrained,
    Partial,
    Fully,
    Redundant,
    Conflicting,
}

#[derive(Debug)]
pub struct Solution {
    pub recipe: Value,
    pub state: State,
    pub dof: usize,
    pub residual: f64,
    pub conflicting: Vec<String>,
}

impl Solution {
    pub fn message(&self) -> String {
        match self.state {
            State::Unconstrained | State::Partial => {
                let noun = if self.dof == 1 { "degree" } else { "degrees" };
                format!("{} {noun} of freedom remaining.", self.dof)
            }
            State::Fully => "Fully constrained.".into(),
            State::Redundant => {
                "Some constraints repeat information. The sketch still solves.".into()
            }
            State::Conflicting => {
                let ids = self.conflicting.join(", ");
                format!("These constraints cannot all be satisfied at once. Check {ids}.")
            }
        }
    }
}

#[derive(Clone, Copy)]
struct Point {
    start: usize,
    fixed: Option<[f64; 2]>,
}

#[derive(Clone, Copy)]
enum Entity {
    Line([Point; 2]),
    Round { centre: Point, radius: usize },
    Ellipse { centre: Point },
    Spline,
}

#[derive(Default)]
struct Layout<'a> {
    points: HashMap<&'a str, Point>,
    entities: HashMap<&'a str, Entity>,
    fields: Vec<(&'static str, usize, &'static str)>,
    values: Vec<f64>,
}

fn entries<'a>(recipe: &'a Value, key: &str) -> Result<&'a [Value], StepError> {
    match recipe.get(key) {
        None => Ok(&[]),
        Some(Value::Array(entries)) => Ok(entries),
        _ => Err(failed(format!("The sketch's {key} must be a list."))),
    }
}

fn text(value: &Value) -> Result<&str, StepError> {
    value
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| failed("The sketch has a missing identifier."))
}

impl<'a> Layout<'a> {
    fn read(recipe: &'a Value) -> Result<Self, StepError> {
        if !recipe.is_object() {
            return Err(failed("This sketch feature has no geometry."));
        }
        let mut layout = Self::default();
        for (index, row) in entries(recipe, "points")?.iter().enumerate() {
            let start = layout.record(row, "points", index, &[("x", None), ("y", None)])?;
            let coordinates = [layout.values[start], layout.values[start + 1]];
            let fixed = row["fixed"]
                .as_bool()
                .unwrap_or(false)
                .then_some(coordinates);
            if layout
                .points
                .insert(text(&row["id"])?, Point { start, fixed })
                .is_some()
            {
                return Err(failed("The sketch has duplicate point identifiers."));
            }
        }
        for (index, row) in entries(recipe, "entities")?.iter().enumerate() {
            let entity = match text(&row["kind"])? {
                "line" => Entity::Line([
                    layout.point(text(&row["start"])?)?,
                    layout.point(text(&row["end"])?)?,
                ]),
                "circle" | "arc" => {
                    let fields = [("radius", None), ("start_angle", None), ("end_angle", None)];
                    let count = if row["kind"] == "arc" { 3 } else { 1 };
                    let radius = layout.record(row, "entities", index, &fields[..count])?;
                    Entity::Round {
                        centre: layout.point(text(&row["center"])?)?,
                        radius,
                    }
                }
                "ellipse" => {
                    layout.record(
                        row,
                        "entities",
                        index,
                        &[("major", None), ("minor", None), ("rotation", Some(0.0))],
                    )?;
                    Entity::Ellipse {
                        centre: layout.point(text(&row["center"])?)?,
                    }
                }
                "spline" => {
                    for point in entries(row, "points")? {
                        layout.point(text(point)?)?;
                    }
                    Entity::Spline
                }
                kind => return Err(failed(format!("Sketch entity '{kind}' is unavailable."))),
            };
            if layout.entities.insert(text(&row["id"])?, entity).is_some() {
                return Err(failed("The sketch has duplicate entity identifiers."));
            }
        }
        Ok(layout)
    }

    fn record(
        &mut self,
        row: &Value,
        list: &'static str,
        index: usize,
        fields: &[(&'static str, Option<f64>)],
    ) -> Result<usize, StepError> {
        let start = self.values.len();
        for &(name, default) in fields {
            let value = match row.get(name) {
                Some(value) => value.as_f64(),
                None => default,
            }
            .filter(|value| value.is_finite())
            .ok_or_else(|| failed(format!("Invalid sketch coordinate or size '{name}'.")))?;
            self.values.push(value);
            self.fields.push((list, index, name));
        }
        Ok(start)
    }

    fn point(&self, id: &str) -> Result<Point, StepError> {
        self.points
            .get(id)
            .copied()
            .ok_or_else(|| failed(format!("Sketch point '{id}' is missing.")))
    }

    fn entity(&self, id: &str) -> Result<Entity, StepError> {
        self.entities
            .get(id)
            .copied()
            .ok_or_else(|| failed(format!("Sketch entity '{id}' is missing.")))
    }
}

struct Equations<'a> {
    constraints: Vec<(&'a str, Constraint)>,
}

impl Equations<'_> {
    fn residuals(&self, values: &[f64]) -> DVector<f64> {
        let mut result = Vec::with_capacity(self.constraints.len());
        for (_, constraint) in &self.constraints {
            result.extend(constraint.residuals(values).iter());
        }
        DVector::from_vec(result)
    }

    fn jacobian(
        &self,
        values: &DVector<f64>,
        base: &DVector<f64>,
    ) -> Result<DMatrix<f64>, StepError> {
        let mut jacobian = DMatrix::zeros(base.len(), values.len());
        let mut stepped = values.clone();
        for (column, value) in values.iter().copied().enumerate() {
            let step = 1e-7 * value.abs().max(1.0);
            stepped[column] = value + step;
            let derivative = (self.residuals(stepped.as_slice()) - base) / step;
            jacobian.set_column(column, &derivative);
            stepped[column] = value;
        }
        if jacobian.iter().all(|v| v.is_finite()) {
            Ok(jacobian)
        } else {
            Err(failed(
                "The sketch constraints produced a non-finite derivative.",
            ))
        }
    }

    fn conflicting(&self, values: &[f64]) -> Vec<String> {
        let mut ranked = self
            .constraints
            .iter()
            .map(|(id, constraint)| {
                let error = constraint
                    .residuals(values)
                    .iter()
                    .map(|v| v.abs())
                    .fold(0.0, f64::max);
                (error, *id)
            })
            .filter(|(error, _)| *error > SOLVE_TOLERANCE)
            .collect::<Vec<_>>();
        ranked.sort_by(|a, b| b.0.total_cmp(&a.0));
        ranked
            .into_iter()
            .take(3)
            .map(|(_, id)| id.to_owned())
            .collect()
    }
}

pub fn solve(recipe: &Value, parameters: &HashMap<String, f64>) -> Result<Solution, StepError> {
    let layout = Layout::read(recipe)?;
    let constraints = entries(recipe, "constraints")?
        .iter()
        .filter(|value| value["driving"].as_bool().unwrap_or(true))
        .map(|value| {
            let id = value["id"]
                .as_str()
                .filter(|id| !id.is_empty())
                .unwrap_or("unnamed constraint");
            Ok((id, Constraint::read(value, &layout, parameters)?))
        })
        .collect::<Result<_, StepError>>()?;
    let equations = Equations { constraints };
    let mut values = DVector::from_vec(layout.values);
    let count = values.len();
    if equations.constraints.is_empty() {
        return Ok(Solution {
            recipe: recipe.clone(),
            state: if count == 0 {
                State::Fully
            } else {
                State::Unconstrained
            },
            dof: count,
            residual: 0.0,
            conflicting: vec![],
        });
    }
    let mut residuals = equations.residuals(values.as_slice());
    if !residuals.iter().all(|v| v.is_finite()) || !residuals.norm_squared().is_finite() {
        return Err(failed(
            "The sketch constraints produced a non-finite residual.",
        ));
    }

    // Levenberg–Marquardt. A diagonal damping term also admits underdetermined
    // sketches, so unconstrained variables can stay close to their saved values.
    let mut damping = 1e-3;
    for _ in 0..200 {
        if residuals.amax() <= ITERATION_TOLERANCE {
            break;
        }
        let jac = equations.jacobian(&values, &residuals)?;
        let mut normal = jac.transpose() * &jac;
        let gradient = jac.transpose() * &residuals;
        if gradient.amax() <= ITERATION_TOLERANCE {
            break;
        }
        for i in 0..count {
            normal[(i, i)] += damping * normal[(i, i)].max(1e-6);
        }
        let Some(step) = normal.cholesky().map(|factor| factor.solve(&-gradient)) else {
            damping *= 10.0;
            continue;
        };
        let next = &values + step;
        let candidate = equations.residuals(next.as_slice());
        if next.iter().all(|v| v.is_finite()) && candidate.norm_squared() < residuals.norm_squared()
        {
            values = next;
            residuals = candidate;
            damping = (damping / 3.0_f64).max(1e-15);
        } else {
            damping *= 10.0;
            if damping > 1e15 {
                break;
            }
        }
    }

    let jac = equations.jacobian(&values, &residuals)?;
    let svd = SVD::try_new(jac, false, false, f64::EPSILON, 1000)
        .ok_or_else(|| failed("Could not determine the sketch's degrees of freedom."))?;
    let rank = svd.rank((svd.singular_values.amax() * 1e-8).max(1e-9));
    let dof = count - rank;
    let residual = residuals.amax();
    let state = if residual > SOLVE_TOLERANCE {
        State::Conflicting
    } else if rank < residuals.len() {
        State::Redundant
    } else if dof == 0 {
        State::Fully
    } else {
        State::Partial
    };
    let mut solved = recipe.clone();
    let mut conflicting = vec![];
    if state == State::Conflicting {
        conflicting = equations.conflicting(values.as_slice());
    } else {
        for ((list, index, field), value) in layout.fields.into_iter().zip(values.iter()) {
            solved[list][index][field] = json!(value);
        }
        let driving = entries(recipe, "constraints")?
            .iter()
            .enumerate()
            .filter(|(_, value)| value["driving"].as_bool().unwrap_or(true));
        for ((index, value), (_, constraint)) in driving.zip(&equations.constraints) {
            if let Some(target) = constraint.target_value() {
                let stored = &mut solved["constraints"][index];
                if value.get("expression").is_none()
                    && let Some(expression) = value["value"].as_str()
                {
                    stored["expression"] = json!(expression);
                }
                stored["value"] = json!(target);
            }
        }
    }
    Ok(Solution {
        recipe: solved,
        state,
        dof,
        residual,
        conflicting,
    })
}

#[cfg(test)]
mod tests;
