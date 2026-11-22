// SimpleCAD 0.4's printing.check_overhangs measure, shared by the print check and Orient each.

/** The share of the surface leaning more than `maxAngle` past vertical, not counting what rests on the plate. */
export function overhangFraction(positions: ArrayLike<number>, indices: ArrayLike<number>, maxAngle: number) {
  // Overhang counts from vertical: a face leaning max past it points down by sin(max).
  const limit = -Math.sin(maxAngle * Math.PI / 180)
  let lowest = Infinity
  for (let i = 0; i < indices.length; i++) lowest = Math.min(lowest, positions[indices[i] * 3 + 2])
  const plate = lowest + 0.3
  let steep = 0
  let total = 0
  const corner = (i: number) => [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]]
  for (let t = 0; t + 2 < indices.length; t += 3) {
    const [a, b, c] = [corner(indices[t]), corner(indices[t + 1]), corner(indices[t + 2])]
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
    const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    const twice = Math.hypot(n[0], n[1], n[2])
    if (!twice) continue
    total += twice / 2
    // A downward face lying on the plate is held up by it.
    if (n[2] / twice < limit && Math.min(a[2], b[2], c[2]) > plate) steep += twice / 2
  }
  return total ? steep / total : 0
}

/** check_overhangs' verdict on a fraction. */
export const overhangSeverity = (fraction: number) => fraction > 0.15 ? 'warning' : fraction > 0.02 ? 'note' : 'ok'
