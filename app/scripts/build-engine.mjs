// Build the native addon for the current platform, and put OCCT's shared libraries beside it: the addon finds them
// there ($ORIGIN / @loader_path rpath, or the DLL's own folder on Windows), in development and in the packaged app.
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readdirSync, readlinkSync, rmSync, symlinkSync, lstatSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'

const app = path.resolve(import.meta.dirname, '..')
const engine = path.resolve(app, '..', 'engine')
const native = path.join(app, 'native')
const occt = process.env.OCCT_DIR ?? path.join(homedir(), '.cache', 'simplecad-occt', '8.0.1')
const geos = process.env.GEOS_DIR ?? path.join(homedir(), '.cache', 'simplecad-geos', '3.13.1')
if (!existsSync(path.join(occt, 'include', 'opencascade'))) {
  throw new Error(`No OCCT install in ${occt}. Build one with engine/occt/build-occt.sh, or set OCCT_DIR.`)
}
const local = path.join(homedir(), '.cargo', 'bin', process.platform === 'win32' ? 'cargo.exe' : 'cargo')
if (!existsSync(path.join(geos, 'include', 'geos_c.h'))) {
  throw new Error(`No GEOS install in ${geos}. Build one with engine/occt/build-geos.sh, or set GEOS_DIR.`)
}
const cargo = existsSync(local) ? local : 'cargo'
const binary = { linux: 'libscad_engine.so', darwin: 'libscad_engine.dylib', win32: 'scad_engine.dll' }[process.platform]
if (!binary) throw new Error(`Unsupported build platform: ${process.platform}`)

execFileSync(cargo, ['build', '--release', '--locked', '-p', 'scad_engine', '--target-dir', 'target'], {
  cwd: engine, stdio: 'inherit', env: { ...process.env, OCCT_DIR: occt, GEOS_DIR: geos }
})
mkdirSync(native, { recursive: true })
for (const stale of readdirSync(native)) if (stale !== '.gitkeep') rmSync(path.join(native, stale), { force: true })
copyFileSync(path.join(engine, 'target', 'release', binary), path.join(native, 'scad_engine.node'))
copyFileSync(path.join(occt, 'share', 'licenses', 'freetype', 'FTL.TXT'), path.join(native, 'FreeType-License.txt'))
copyFileSync(path.join(geos, 'share', 'licenses', 'geos', 'COPYING'), path.join(native, 'GEOS-License.txt'))

// Every OCCT library, as installed: real files copied, version symlinks recreated rather than copied three times.
const shared = {
  linux: /^lib(TK\w+|geos(?:_c)?)\.so[.\d]*$/,
  darwin: /^lib(TK\w+|geos(?:_c)?)[.\d]*\.dylib$/,
  win32: /^(TK\w+|geos(?:_c)?)\.dll$/
}[process.platform]
for (const prefix of [occt, geos]) {
  const libraries = path.join(prefix, process.platform === 'win32' ? 'bin' : 'lib')
  for (const name of readdirSync(libraries).filter((n) => shared.test(n))) {
    const from = path.join(libraries, name)
    if (lstatSync(from).isSymbolicLink()) symlinkSync(readlinkSync(from), path.join(native, name))
    else copyFileSync(from, path.join(native, name))
  }
}
