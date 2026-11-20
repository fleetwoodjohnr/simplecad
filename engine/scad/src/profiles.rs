//! Extruded profiles from Python's `primitives` and `decorative` modules.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, StepError, failed};
use std::f64::consts::{FRAC_PI_2, PI, TAU};

pub(crate) fn profile(
    kind: &str,
    ctx: &Context,
) -> Result<occt::UniquePtr<occt::Shape>, StepError> {
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let dimension = |key, default| positive(length(key, default)?, key);
    if kind == "polygon_prism" {
        let sides = length("sides", 6.0)?.trunc() as usize;
        let height = dimension("height", 20.0)?;
        let across_flats = length("across_flats", 0.0)?;
        let mut radius = length("radius", 15.0)?;
        if sides < 3 {
            return Err(failed(
                "A polygon needs at least three sides. Try 6 for a hexagon.",
            ));
        }
        let across_corners = length("across_corners", 0.0)?;
        if across_flats > 0.0 {
            radius = across_flats / (2.0 * (PI / sides as f64).cos());
        } else if across_corners > 0.0 {
            radius = across_corners / 2.0;
        }
        let radius = positive(radius, "size")?;
        let mut points = Vec::new();
        points
            .try_reserve_exact(sides.saturating_mul(2))
            .map_err(|_| failed("There are too many polygon sides to build. Use fewer sides."))?;
        for index in 0..sides {
            let angle = TAU * index as f64 / sides as f64;
            points.extend([radius * angle.cos(), radius * angle.sin()]);
        }
        return occt::polygon_profile(&points, height).map_err(|error| failed(error.what()));
    }
    if matches!(kind, "oval" | "pill") {
        let width = dimension("width", 40.0)?;
        let depth = dimension("depth", if kind == "oval" { 25.0 } else { 15.0 })?;
        let height = dimension("height", 10.0)?;
        let shape = if kind == "oval" {
            occt::oval(width, depth, height)
        } else {
            if (width - depth).abs() / 2.0 < 1e-6 {
                return Err(failed(
                    "A pill must be longer than it is wide. Make the width and depth different, or use Cylinder for a round one.",
                ));
            }
            occt::pill(width, depth, height)
        };
        return shape.map_err(|error| failed(error.what()));
    }

    let size = dimension("size", 30.0)?;
    let height = dimension("height", 10.0)?;
    let shape = match kind {
        "star" => {
            let ratio = ctx.value("inner_ratio", 0.45, Dimension::Scalar)?;
            if !(ratio > 0.05 && ratio < 0.95) {
                return Err(failed(
                    "The star inset must be between 0.05 and 0.95. Try 0.45 for a classic five-point star.",
                ));
            }
            let points = std::array::from_fn::<_, 10, _>(|index| {
                let angle = FRAC_PI_2 + index as f64 * PI / 5.0;
                let radius = if index % 2 == 0 { 1.0 } else { ratio };
                [radius * angle.cos(), radius * angle.sin()]
            });
            occt::polygon_profile(&normalise(&points, size), height)
        }
        "heart" => {
            let points = std::array::from_fn::<_, 128, _>(|index| {
                let angle = TAU * index as f64 / 128.0;
                let x = 16.0 * angle.sin().powf(3.0);
                let y = 13.0 * angle.cos()
                    - 5.0 * (2.0 * angle).cos()
                    - 2.0 * (3.0 * angle).cos()
                    - (4.0 * angle).cos();
                [x, y]
            });
            occt::polygon_profile(&normalise(&points, size), height)
        }
        "cross" => {
            let arm = length("arm_width", size / 3.0)?;
            if arm <= 0.0 || arm >= size {
                return Err(failed(format!(
                    "The cross arm width must be smaller than its size. Use an arm width between 0 and {size:.2} mm."
                )));
            }
            let (half, narrow) = (size / 2.0, arm / 2.0);
            let points = [
                [-narrow, half],
                [narrow, half],
                [narrow, narrow],
                [half, narrow],
                [half, -narrow],
                [narrow, -narrow],
                [narrow, -half],
                [-narrow, -half],
                [-narrow, -narrow],
                [-half, -narrow],
                [-half, narrow],
                [-narrow, narrow],
            ];
            occt::polygon_profile(points.as_flattened(), height)
        }
        "crescent" => {
            let thickness = length("thickness", size * 0.27)?;
            if thickness <= 0.0 || thickness >= size {
                return Err(failed(format!(
                    "The crescent thickness must be smaller than its size. Use a thickness between 0 and {size:.2} mm."
                )));
            }
            occt::crescent(size, height, thickness)
        }
        "lightning" => {
            let points = [
                [0.20, 1.0],
                [-0.55, 0.05],
                [-0.12, 0.05],
                [-0.42, -1.0],
                [0.62, -0.02],
                [0.18, -0.02],
            ];
            occt::polygon_profile(&normalise(&points, size), height)
        }
        _ => unreachable!("unknown profile kind: {kind}"),
    };
    shape.map_err(|error| failed(error.what()))
}

fn positive(value: f64, name: &str) -> Result<f64, StepError> {
    if value <= 0.0 {
        return Err(failed(format!(
            "The {name} must be greater than zero. Enter a positive {name}."
        )));
    }
    Ok(value)
}

/// Centre the silhouette and scale its larger span to the requested size.
fn normalise(points: &[[f64; 2]], size: f64) -> Vec<f64> {
    let mut low = points[0];
    let mut high = points[0];
    for [x, y] in points {
        low = [low[0].min(*x), low[1].min(*y)];
        high = [high[0].max(*x), high[1].max(*y)];
    }
    let scale = size / (high[0] - low[0]).max(high[1] - low[1]);
    let centre = [(low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0];
    points
        .iter()
        .flat_map(|[x, y]| [(x - centre[0]) * scale, (y - centre[1]) * scale])
        .collect()
}
