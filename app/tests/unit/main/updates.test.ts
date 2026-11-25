import { generateKeyPairSync, sign } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { DAY, dueForCheck, installCommand, releaseFromGitHub, verifiedChecksum, updateAsset, windowsUpdateFlags } from '../../../src/main/updates'

describe('GitHub updates', () => {
  it('selects only newer stable releases and legacy-compatible package names', () => {
    const data = { tag_name: 'v0.10.0', draft: false, prerelease: false, assets: [
      { name: 'SHA256SUMS' }, { name: 'SHA256SUMS.sig' }, { name: 'SimpleCAD-0.10.0-linux-x86_64.AppImage' }
    ] }
    const release = releaseFromGitHub(data, '0.9.9')!
    expect(updateAsset(release, 'appimage', 'x64')).toBe('SimpleCAD-0.10.0-linux-x86_64.AppImage')
    expect(updateAsset(release, 'windows', 'x64')).toBeUndefined()
    expect(releaseFromGitHub({ ...data, draft: true }, '0.9.9')).toBeUndefined()
    expect(releaseFromGitHub({ ...data, prerelease: true }, '0.9.9')).toBeUndefined()
    expect(releaseFromGitHub(data, '0.10.1')).toBeUndefined()
  })

  it('uses the Ed25519 checksum format already trusted by older builds', () => {
    const { privateKey, publicKey } = generateKeyPairSync('ed25519')
    const rawKey = publicKey.export({ type: 'spki', format: 'der' }).subarray(-32)
    const name = 'SimpleCAD-0.4.0-linux-x86_64.AppImage'
    const hash = 'f'.repeat(64)
    const content = Buffer.from(`${hash}  ${name}\n`)
    const signature = Buffer.from(sign(null, content, privateKey).toString('base64') + '\n')
    expect(verifiedChecksum(content, signature, rawKey, name)).toBe(hash)
    expect(() => verifiedChecksum(Buffer.from(content.toString().replace('f', 'a')), signature, rawKey, name)).toThrow(/signature/)
    expect(() => verifiedChecksum(content, signature, rawKey, 'other.AppImage')).toThrow(/checksum/)
  })
})

it('refuses a corrupted installer and supports an atomic AppImage swap and rollback', async () => {
  const { createHash } = await import('node:crypto')
  const { mkdtemp, readdir, readFile, rm, writeFile } = await import('node:fs/promises')
  const { tmpdir } = await import('node:os')
  const path = await import('node:path')
  const { downloadUpdate, replaceAppImage, rollbackAppImage } = await import('../../../src/main/updates')
  const { privateKey, publicKey } = generateKeyPairSync('ed25519')
  const key = publicKey.export({ type: 'spki', format: 'der' }).subarray(-32)
  const name = 'SimpleCAD-0.4.0-linux-x86_64.AppImage'
  const version = '0.4.0'
  const expected = createHash('sha256').update('new image').digest('hex')
  const assets = new Map([
    [name, 'corrupted image'], ['SHA256SUMS', `${expected}  ${name}\n`]
  ])
  assets.set('SHA256SUMS.sig', sign(null, Buffer.from(assets.get('SHA256SUMS')!), privateKey).toString('base64'))
  const release = { version, notes: '', assets: new Set(assets.keys()) }
  const fakeFetch: typeof fetch = async (url) => new Response(assets.get(String(url).split('/').at(-1)!)!)
  const folder = await mkdtemp(path.join(tmpdir(), 'simplecad-update-test-'))
  try {
    await expect(downloadUpdate(release, name, folder, key, fakeFetch)).rejects.toThrow(/signed checksum/)
    expect(await readdir(folder)).toEqual([])
    const disconnected: typeof fetch = async (url, options) => {
      if (!String(url).endsWith(name)) return fakeFetch(url, options)
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(Buffer.from('new '))
        },
        pull(controller) {
          controller.error(new Error('Connection lost'))
        }
      }))
    }
    await expect(downloadUpdate(release, name, folder, key, disconnected)).rejects.toThrow('Connection lost')
    expect(await readdir(folder)).toEqual([])

    const cancel = new AbortController()
    const slow: typeof fetch = async (url, options) => {
      if (!String(url).endsWith(name)) return fakeFetch(url, options)
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(Buffer.from('new '))
          setTimeout(() => cancel.abort(), 20)
        }
      }))
    }
    await expect(downloadUpdate(release, name, folder, key, slow, cancel.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(await readdir(folder)).toEqual([])

    assets.set(name, 'new image')
    const target = path.join(folder, 'existing.AppImage')
    await writeFile(target, 'old image', { mode: 0o755 })
    const packageData = await downloadUpdate(release, name, folder, key, fakeFetch)
    await replaceAppImage(packageData.installer, target, packageData.expected)
    expect(await readFile(target, 'utf8')).toBe('new image')
    await rollbackAppImage(target)
    expect(await readFile(target, 'utf8')).toBe('old image')
  } finally {
    await rm(folder, { recursive: true, force: true })
  }
})

describe('automatic updates', () => {
  it('checks by itself at most once a day, and again if the clock went back', () => {
    const now = 1_800_000_000_000
    expect(dueForCheck(0, now)).toBe(true)
    expect(dueForCheck(now - DAY + 1000, now)).toBe(false)
    expect(dueForCheck(now - DAY, now)).toBe(true)
    expect(dueForCheck(now + 60_000, now)).toBe(true)
  })
  it('installs a verified package with the system package manager, asking for the password unless root', () => {
    expect(installCommand('rpm', '/u/SimpleCAD.rpm', false)).toEqual(['pkexec', ['dnf', 'install', '-y', '/u/SimpleCAD.rpm']])
    expect(installCommand('deb', '/u/SimpleCAD.deb', false)).toEqual(['pkexec', ['apt-get', 'install', '-y', '/u/SimpleCAD.deb']])
    expect(installCommand('rpm', '/u/SimpleCAD.rpm', true)).toEqual(['dnf', ['install', '-y', '/u/SimpleCAD.rpm']])
  })
  it('runs the Windows installer with a progress window only, and has it start SimpleCAD again', () => {
    expect(windowsUpdateFlags).toEqual(['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/RELAUNCH'])
  })
})
