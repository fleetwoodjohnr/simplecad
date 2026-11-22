// SimpleCAD 0.4's arrow-key nudge (ui/main_window.py): exact steps along model axes.
import type { Vec3, ViewFrame } from '@/shared/engine-protocol'

/** NUDGE_STEP_MM: one arrow press, mm. */
export const NUDGE_STEP = 0.25

/** `_world_axes`: the signed world axes nearest screen right, up and depth. Right picks first, then up from what
 * is left, and depth gets the last, so a press in the default iso view is a round step along one axis. */
export function worldAxes({ direction: f, up: u }: ViewFrame): [Vec3, Vec3, Vec3] {
  const right = [f[1] * u[2] - f[2] * u[1], f[2] * u[0] - f[0] * u[2], f[0] * u[1] - f[1] * u[0]]
  const remaining = [0, 1, 2]
  return [right, u, f].map((vector) => {
    const index = remaining.reduce((best, i) => Math.abs(vector[i]) > Math.abs(vector[best]) ? i : best)
    remaining.splice(remaining.indexOf(index), 1)
    const axis: Vec3 = [0, 0, 0]
    axis[index] = vector[index] >= 0 ? 1 : -1
    return axis
  }) as [Vec3, Vec3, Vec3]
}
