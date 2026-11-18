//! A small enclosure with a recessed vent and two through holes.
use crate::{Boolean, Bounds, Solid};

pub fn recessed_enclosure() -> Result<Solid, &'static str> {
    let block = |min, max| Solid::cuboid(Bounds { min, max });
    block([0., 0., 0.], [120., 80., 55.])?
        .boolean(&block([4., 4., 4.], [116., 76., 56.])?, Boolean::Subtract)?
        .boolean(&block([40., 75., 10.], [74., 78., 44.])?, Boolean::Subtract)?
        .boolean(&block([50., 77., 20.], [54., 81., 24.])?, Boolean::Subtract)?
        .boolean(&block([15., 75., 45.], [19., 81., 49.])?, Boolean::Subtract)
}
