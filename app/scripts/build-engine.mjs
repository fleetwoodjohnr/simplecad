// Build the native addon for the current platform.
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'

const app = path.resolve(import.meta.dirname, '..')
const engine = path.resolve(app, '..', 'engine')
const native = path.join(app, 'native')
const local = path.join(homedir(), '.cargo', 'bin', process.platform === 'win32' ? 'cargo.exe' : 'cargo')
const cargo = existsSync(local) ? local : 'cargo'
const binary = { linux: 'libscad_engine.so', darwin: 'libscad_engine.dylib', win32: 'scad_engine.dll' }[process.platform]
if (!binary) throw new Error(`Unsupported build platform: ${process.platform}`)

execFileSync(cargo, ['build', '--release', '--locked', '-p', 'scad_engine', '--target-dir', 'target'], { cwd: engine, stdio: 'inherit' })
mkdirSync(native, { recursive: true })
copyFileSync(path.join(engine, 'target', 'release', binary), path.join(native, 'scad_engine.node'))
