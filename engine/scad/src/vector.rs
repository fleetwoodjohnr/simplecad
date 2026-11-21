use crate::rebuild::{StepError, failed};

pub(crate) type V = [f64; 3];

pub(crate) fn sub(a: V, b: V) -> V {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

pub(crate) fn add(a: V, b: V) -> V {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

pub(crate) fn scale(a: V, k: f64) -> V {
    [a[0] * k, a[1] * k, a[2] * k]
}

pub(crate) fn dot(a: V, b: V) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

pub(crate) fn cross(a: V, b: V) -> V {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

pub(crate) fn length(a: V) -> f64 {
    dot(a, a).sqrt()
}

pub(crate) fn unit(a: V) -> Result<V, StepError> {
    let magnitude = length(a);
    if magnitude < 1e-12 {
        return Err(failed("That geometry has no usable direction."));
    }
    Ok(scale(a, 1.0 / magnitude))
}

pub(crate) fn perpendicular(a: V) -> Result<V, StepError> {
    let reference = if a[0].abs() < 0.9 {
        [1.0, 0.0, 0.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    unit(cross(a, reference))
}
