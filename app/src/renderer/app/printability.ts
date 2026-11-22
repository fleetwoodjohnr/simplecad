// SimpleCAD 0.4's print checks that read the triangulation (kernel/printing.py): build volume, plate contact and
// overhangs, run here on the display mesh. ponytail: validity, disconnected parts, thin walls and tiny features
// need the B-rep and are not checked yet.
import type { MeshData } from '@/shared/engine-protocol'

/** SimpleCAD 0.4's bundled default printer (data/printers/elegoo_centauri_carbon_2.json). */
export const defaultPrinter = { name: 'Elegoo Centauri Carbon 2', size: [256, 256, 256], maxOverhang: 50 }

export interface Finding { check: string; severity: 'ok' | 'note' | 'warning'; message: string; hint?: string }

const fixed = (value: number) => value.toFixed(1)

/** The checks over every shown body's triangles. */
export function printChecks(mesh: Pick<MeshData, 'positions' | 'indices'>, printer = defaultPrinter): Finding[] {
  const { positions, indices } = mesh
  if (!indices.length) return [{ check: 'empty', severity: 'note', message: 'There is nothing to print yet.' }]
  const low = [Infinity, Infinity, Infinity]
  const high = [-Infinity, -Infinity, -Infinity]
  for (const index of indices) {
    for (let axis = 0; axis < 3; axis++) {
      const value = positions[index * 3 + axis]
      low[axis] = Math.min(low[axis], value)
      high[axis] = Math.max(high[axis], value)
    }
  }
  const size = low.map((value, axis) => high[axis] - value)
  const findings: Finding[] = []

  const over = size.flatMap((value, axis) => value > printer.size[axis] + 1e-6
    ? [`${'XYZ'[axis]} is ${fixed(value)} mm, the plate allows ${printer.size[axis].toFixed(0)} mm`] : [])
  findings.push(over.length
    ? { check: 'build_volume', severity: 'warning', message: `This is bigger than the ${printer.name}'s build volume.`, hint: over.join('; ') }
    : { check: 'build_volume', severity: 'ok', message: `Fits the ${printer.name}: ${size.map(fixed).join(' x ')} mm.` })

  const lowest = low[2]
  findings.push(lowest < -1e-3
    ? { check: 'on_plate', severity: 'warning', message: `Part of the model is ${fixed(Math.abs(lowest))} mm below the build plate.` }
    : lowest > 0.05
      ? { check: 'on_plate', severity: 'note', message: `The model floats ${fixed(lowest)} mm above the plate.` }
      : { check: 'on_plate', severity: 'ok', message: 'Sitting on the build plate.' })

  // Overhang counts from vertical: a face leaning max past it points down by sin(max). A face resting on the
  // plate is held up by it.
  const limit = -Math.sin(printer.maxOverhang * Math.PI / 180)
  const plate = lowest + 0.3
  let steep = 0
  let total = 0
  const corner = (i: number) => [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]]
  for (let t = 0; t < indices.length; t += 3) {
    const [a, b, c] = [corner(indices[t]), corner(indices[t + 1]), corner(indices[t + 2])]
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
    const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    const twice = Math.hypot(n[0], n[1], n[2])
    if (!twice) continue
    total += twice / 2
    if (n[2] / twice < limit && Math.min(a[2], b[2], c[2]) > plate) steep += twice / 2
  }
  const fraction = total ? steep / total : 0
  const share = `${(fraction * 100).toFixed(0)}% of the surface overhangs by more than ${printer.maxOverhang.toFixed(0)}°.`
  findings.push(fraction > 0.15 ? { check: 'overhangs', severity: 'warning', message: share, hint: 'It will need supports, or rotating.' }
    : fraction > 0.02 ? { check: 'overhangs', severity: 'note', message: share, hint: 'Light support may help.' }
    : { check: 'overhangs', severity: 'ok', message: 'No significant overhangs.' })
  return findings
}
