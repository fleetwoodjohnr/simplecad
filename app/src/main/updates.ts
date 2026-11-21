import { createHash, createPublicKey, verify } from 'node:crypto'
import { constants, createReadStream, createWriteStream } from 'node:fs'
import { chmod, copyFile, mkdir, mkdtemp, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { Readable, Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import type { ReadableStream as NodeWebStream } from 'node:stream/web'

const REPO = 'fleetwoodjohnr/simplecad'
export const RELEASES = `https://github.com/${REPO}/releases`
const SUMS = 'SHA256SUMS'
const SIGNATURE = 'SHA256SUMS.sig'
const ED25519_SPKI = Buffer.from('302a300506032b6570032100', 'hex')

export type InstallKind = 'appimage' | 'deb' | 'rpm' | 'windows' | 'macos' | 'flatpak' | 'source'
export type Release = { version: string; notes: string; assets: Set<string> }
type Download = typeof fetch

function stableVersion(text: unknown): string[] | undefined {
  if (typeof text !== 'string') return
  return /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(text)?.slice(1)
}

export function releaseFromGitHub(data: unknown, current: string): Release | undefined {
  if (!data || typeof data !== 'object') return
  const json = data as Record<string, unknown>
  const next = stableVersion(json.tag_name)
  const installed = stableVersion(current)
  if (json.draft !== false || json.prerelease !== false || !next || !installed) return
  const firstDifference = next.findIndex((part, index) => part !== installed[index])
  if (firstDifference < 0 || BigInt(next[firstDifference]) <= BigInt(installed[firstDifference])) return
  const assets = new Set<string>()
  if (Array.isArray(json.assets)) {
    for (const asset of json.assets) if (typeof asset?.name === 'string') assets.add(asset.name)
  }
  return { version: next.join('.'), notes: typeof json.body === 'string' ? json.body : '', assets }
}

export function updateAsset(release: Release, kind: InstallKind, machine: string): string | undefined {
  const arch = machine === 'x64' ? 'x86_64' : machine === 'arm64' ? 'arm64' : undefined
  if (!arch) return
  const names = {
    appimage: `linux-${arch}.AppImage`, deb: `linux-${arch}.deb`, rpm: `linux-${arch}.rpm`,
    windows: `windows-${arch}-setup.exe`, macos: `macos-${arch}.dmg`
  }
  if (kind === 'source' || kind === 'flatpak') return
  const name = `SimpleCAD-${release.version}-${names[kind]}`
  if (release.assets.has(name)) return name
}

export function verifiedChecksum(sums: Buffer, signature: Buffer, key: Buffer, name: string): string {
  const encoded = signature.toString('ascii').trim()
  const raw = signature.byteLength === 64 ? signature : /^[A-Za-z0-9+/]{86}==$/.test(encoded) ? Buffer.from(encoded, 'base64') : Buffer.alloc(0)
  if (key.byteLength !== 32 || raw.byteLength !== 64 || !verify(null, sums,
    createPublicKey({ key: Buffer.concat([ED25519_SPKI, key]), type: 'spki', format: 'der' }), raw)) {
    throw new Error('The release signature does not match SimpleCAD’s update key.')
  }
  const matches = sums.toString('utf8').split(/\r?\n/).map((line) => /^([0-9a-fA-F]{64})\s+\*?(\S+)$/.exec(line))
    .filter((match) => match?.[2] === name)
  if (matches.length !== 1) throw new Error('The signed checksums do not identify this package.')
  return matches[0]![1].toLowerCase()
}

async function request(url: string, download: Download, version: string, timeout: number, cancel?: AbortSignal) {
  const deadline = AbortSignal.timeout(timeout)
  const signal = cancel ? AbortSignal.any([cancel, deadline]) : deadline
  signal.throwIfAborted()
  const response = await download(url, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': `SimpleCAD/${version}` },
    signal
  })
  if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status}.`)
  return response
}

export async function checkRelease(current: string, download: Download = fetch) {
  const response = await request(`https://api.github.com/repos/${REPO}/releases/latest`, download, current, 15000)
  return releaseFromGitHub(await response.json(), current)
}

export async function downloadUpdate(release: Release, name: string, folder: string, key: Buffer,
  download: Download = fetch, cancel?: AbortSignal) {
  if (![name, SUMS, SIGNATURE].every((file) => release.assets.has(file))) {
    throw new Error('The release is missing its installer or signed checksums.')
  }
  const asset = (file: string, timeout: number) => request(
    `${RELEASES}/download/v${release.version}/${encodeURIComponent(file)}`, download, release.version, timeout, cancel
  )
  const sums = Buffer.from(await (await asset(SUMS, 30000)).arrayBuffer())
  const signature = Buffer.from(await (await asset(SIGNATURE, 30000)).arrayBuffer())
  const expected = verifiedChecksum(sums, signature, key, name)
  await mkdir(folder, { recursive: true })
  const temporary = await mkdtemp(path.join(folder, 'download-'))
  const partial = path.join(temporary, name)
  try {
    const response = await asset(name, 600000)
    if (!response.body) throw new Error('GitHub returned an empty package.')
    const hash = createHash('sha256')
    const digest = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        hash.update(chunk)
        callback(null, chunk)
      }
    })
    // Fetch and Node use the same WHATWG stream at runtime, with separate BYOB
    // typings. Destroying the adapter also cancels a pending read on that stream.
    const body = Readable.fromWeb(response.body as unknown as NodeWebStream<Uint8Array>)
    await pipeline(body, digest, createWriteStream(partial, { flags: 'wx', mode: 0o600 }), { signal: cancel })
    cancel?.throwIfAborted()
    if (hash.digest('hex') !== expected) throw new Error('The package does not match its signed checksum.')
    return { installer: partial, expected }
  } catch (err) {
    await rm(temporary, { recursive: true, force: true })
    throw err
  }
}

export async function replaceAppImage(installer: string, target: string, expected: string) {
  const temporary = await mkdtemp(path.join(path.dirname(target), '.simplecad-update-'))
  const staged = path.join(temporary, 'install.AppImage')
  const backup = path.join(temporary, 'backup.AppImage')
  try {
    await copyFile(installer, staged, constants.COPYFILE_EXCL)
    const hash = createHash('sha256')
    for await (const chunk of createReadStream(staged)) hash.update(chunk)
    if (hash.digest('hex') !== expected) throw new Error('The staged AppImage does not match its signed checksum.')
    await chmod(staged, 0o755)
    await copyFile(target, backup)
    await rename(backup, `${target}.bak`)
    await rename(staged, target)
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}

export function rollbackAppImage(target: string) {
  return rename(`${target}.bak`, target)
}
