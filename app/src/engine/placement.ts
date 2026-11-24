import type { PartDisplay } from './document'

/** `to` after undoing `from`: the move from one rigid placement to another, or undefined for one that scales or
 * mirrors. Both are 3×4 matrices, row by row. */
export function relativePlacement(from: number[], to: number[]): number[] | undefined {
  const rigid = (m: number[]) => {
    const rows = [0, 4, 8].map((i) => [m[i], m[i + 1], m[i + 2]])
    const det = rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1]) - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0]) +
      rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    return Math.abs(det - 1) < 1e-9 && rows.every((row, i) => rows.every((other, j) =>
      Math.abs(row[0] * other[0] + row[1] * other[1] + row[2] * other[2] - (i === j ? 1 : 0)) < 1e-9))
  }
  if (!rigid(from) || !rigid(to)) return undefined
  // from⁻¹ = [Rᵀ | -Rᵀt]
  const inverse: number[] = []
  for (let row = 0; row < 3; row++) {
    const column = [from[row], from[4 + row], from[8 + row]]
    inverse.push(...column, -(column[0] * from[3] + column[1] * from[7] + column[2] * from[11]))
  }
  const out: number[] = []
  for (let row = 0; row < 3; row++) {
    const r = to.slice(row * 4, row * 4 + 4)
    for (let column = 0; column < 4; column++) {
      out.push(r[0] * inverse[column] + r[1] * inverse[4 + column] + r[2] * inverse[8 + column] + (column === 3 ? r[3] : 0))
    }
  }
  return out
}

/** A body's display moved rigidly by `m` (3×4, row by row): what its moved copy displays, without meshing it again. */
export function movedDisplay(display: PartDisplay, m: number[]): PartDisplay {
  const apply = (source: Float32Array, translate: boolean) => {
    const out = new Float32Array(source.length)
    for (let i = 0; i < source.length; i += 3) {
      const x = source[i], y = source[i + 1], z = source[i + 2]
      for (let row = 0; row < 3; row++) out[i + row] = m[row * 4] * x + m[row * 4 + 1] * y + m[row * 4 + 2] * z + (translate ? m[row * 4 + 3] : 0)
    }
    return out
  }
  const vector = (v: [number, number, number], translate: boolean) =>
    [0, 1, 2].map((row) => m[row * 4] * v[0] + m[row * 4 + 1] * v[1] + m[row * 4 + 2] * v[2] + (translate ? m[row * 4 + 3] : 0)) as [number, number, number]
  return {
    ...display,
    positions: apply(display.positions, true),
    normals: apply(display.normals, false),
    edgePoints: apply(display.edgePoints, true),
    faces: display.faces.map((face) => ({ ...face, center: vector(face.center, true), anchor: vector(face.anchor, true), normal: vector(face.normal, false) }))
  }
}
