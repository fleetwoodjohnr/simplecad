// SimpleCAD 0.4's print checks that read the triangulation (kernel/printing.py): build volume, plate contact and
// overhangs, run here on the display mesh; the B-rep checks come from the engine (healthChecks).
import type { BodyHealth, MeshData } from '@/shared/engine-protocol'
import { defaultPrinter, overhangFraction, overhangSeverity } from '@/shared/printing'


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

  const fraction = overhangFraction(positions, indices, printer.maxOverhang)
  const share = `${(fraction * 100).toFixed(0)}% of the surface overhangs by more than ${printer.maxOverhang.toFixed(0)}°.`
  const severity = overhangSeverity(fraction)
  findings.push(severity === 'warning' ? { check: 'overhangs', severity, message: share, hint: 'It will need supports, or rotating.' }
    : severity === 'note' ? { check: 'overhangs', severity, message: share, hint: 'Light support may help.' }
    : { check: 'overhangs', severity, message: 'No significant overhangs.' })
  return findings
}

/** SimpleCAD 0.4's check_geometry, check_disconnected, check_thin_walls and check_tiny_features, from each shown
 * body's B-rep, all-clears said once. */
export function healthChecks(bodies: BodyHealth[], printer = defaultPrinter): Finding[] {
  const findings: Finding[] = []
  const seen = new Set<string>()
  const add = (finding: Finding) => {
    const key = `${finding.check}:${finding.message}`
    if (finding.severity === 'ok' && seen.has(key)) return
    seen.add(key)
    findings.push(finding)
  }
  for (const body of bodies) {
    add(!body.valid
      ? { check: 'geometry', severity: 'warning', message: `${body.name} has invalid geometry.`, hint: 'Slicers may produce odd results. Check the most recent features.' }
      : body.openEdges
        ? { check: 'geometry', severity: 'warning', message: `${body.name}'s surface is not closed — ${body.openEdges} open edge(s).`, hint: 'A printer needs a watertight solid.' }
        : { check: 'geometry', severity: 'ok', message: 'Solid and watertight.' })
    add(body.solids > 1
      ? { check: 'disconnected', severity: 'note', message: `${body.name} is ${body.solids} separate pieces.`, hint: 'They will print as separate objects unless they are joined.' }
      : { check: 'disconnected', severity: 'ok', message: 'One connected solid.' })
    const wall = body.thinnestWall
    add(wall === undefined || wall === null
      ? { check: 'thin_walls', severity: 'ok', message: 'No thin walls found.' }
      : wall < printer.minWall
        ? { check: 'thin_walls', severity: 'warning', message: `${body.name}'s thinnest wall is ${wall.toFixed(2)} mm.`,
            hint: `Below ${printer.minWall.toFixed(2)} mm this printer will struggle. Thicken it, or accept a weak wall.` }
        : { check: 'thin_walls', severity: 'ok', message: `Thinnest wall ${wall.toFixed(2)} mm.` })
    add(body.shortEdges.length
      ? { check: 'tiny_features', severity: 'note', message: `${body.name} has ${body.shortEdges.length} feature(s) smaller than ${printer.minFeature.toFixed(2)} mm.`,
          hint: `The smallest is ${Math.min(...body.shortEdges).toFixed(3)} mm and will not print cleanly.` }
      : { check: 'tiny_features', severity: 'ok', message: 'No features below the nozzle size.' })
  }
  return findings
}
