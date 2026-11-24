import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { chmod, copyFile, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { replaceAppImage, rollbackAppImage } from '../app/src/main/updates.ts'

// The baseline's version: a 0.4.4 AppImage by default, or a 0.5.x one.
const [baseline, candidate, version, baselineVersion = '0.4.4'] = process.argv.slice(2)
assert(baseline && candidate && version,
  'Usage: node scripts/appimage_upgrade_drill.mjs <verified-baseline.AppImage> <candidate.AppImage> <candidate-version>')

async function sha256(file) {
  const digest = createHash('sha256')
  for await (const bytes of createReadStream(file)) digest.update(bytes)
  return digest.digest('hex')
}

const scratch = await mkdtemp(path.join(tmpdir(), 'simplecad-appimage-'))
const target = path.join(scratch, 'SimpleCAD.AppImage')
const settings = path.join(scratch, 'settings.json')
const model = path.join(scratch, 'Model.scad3')
const env = {
  ...process.env,
  APPIMAGE_EXTRACT_AND_RUN: '1',
  SIMPLECAD_LEGACY_SETTINGS: settings,
  SIMPLECAD_USER_DATA: path.join(scratch, 'renderer'),
  SIMPLECAD_RECOVERY_DIR: path.join(scratch, 'recovery'),
  QT_QPA_PLATFORM: 'offscreen',
  XDG_CONFIG_HOME: path.join(scratch, 'config'),
  XDG_DATA_HOME: path.join(scratch, 'data'),
}

try {
  await copyFile(baseline, target)
  await chmod(target, 0o755)
  await writeFile(settings, '{"theme":"dark"}\n')
  await copyFile(new URL('../app/tests/e2e/fixtures/features.scad3', import.meta.url), model)
  const original = {
    app: await sha256(baseline),
    model: await sha256(model),
    settings: await sha256(settings),
  }

  function run(flag) {
    return execFileSync(target, [flag], { env, encoding: 'utf8', timeout: 120_000 }).trim()
  }

  async function check(expectedVersion) {
    assert.equal(run('--version').split(/\s+/).at(-1), expectedVersion)
    console.log(run('--self-test'))
    assert.equal(await sha256(settings), original.settings)
    assert.equal(await sha256(model), original.model)
  }

  await check(baselineVersion)
  await assert.rejects(replaceAppImage(candidate, target, '00'.repeat(32)), /signed checksum/)
  assert.equal(await sha256(target), original.app)
  console.log('Rejected the wrong checksum without replacing the installed AppImage.')

  const expected = await sha256(candidate)
  await replaceAppImage(candidate, target, expected)
  assert.equal(await sha256(target), expected)
  assert.equal(await sha256(target + '.bak'), original.app)
  await check(version)

  await rollbackAppImage(target)
  assert.equal(await sha256(target), original.app)
  await check(baselineVersion)
  console.log('AppImage upgrade and byte-for-byte rollback passed.')
} finally {
  await rm(scratch, { recursive: true, force: true })
}
