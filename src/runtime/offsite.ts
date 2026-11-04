/**
 * Off-host encrypted backup copies. Each complete backup directory is bundled,
 * encrypted with AES-256-GCM under a fresh random key, and that key is sealed
 * with the operator's RSA public key (OAEP, SHA-256). Only the operator's
 * offline private key can open an archive; the host keeps only the public key.
 * Archives are uploaded to S3-compatible storage with AWS Signature V4.
 */
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createHmac,
  generateKeyPairSync,
  privateDecrypt,
  publicEncrypt,
  randomBytes,
  constants as cryptoConstants,
} from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { basename, join } from "node:path";
import type { BackupSettings, Manifest } from "./backup.ts";

const MAGIC = Buffer.from("WMBK1\n");
const MAX_ARCHIVE = 1024 * 1024 * 1024;

export interface OffsiteSettings {
  endpoint: string;
  bucket: string;
  region: string;
  accessKeyId: string;
  secretAccessKey: string;
  prefix: string;
  publicKeyPem: string;
}

export function offsiteSettings(
  env: NodeJS.ProcessEnv,
): OffsiteSettings | null {
  const names = [
    "WIDEMIND_OFFSITE_ENDPOINT",
    "WIDEMIND_OFFSITE_BUCKET",
    "WIDEMIND_OFFSITE_ACCESS_KEY_ID",
    "WIDEMIND_OFFSITE_SECRET_ACCESS_KEY",
    "WIDEMIND_OFFSITE_PUBLIC_KEY",
  ];
  if (!names.some((name) => env[name])) return null;
  const missing = names.filter((name) => !env[name]);
  if (missing.length)
    throw new Error(`Off-host backups need ${missing.join(", ")}`);
  const endpoint = new URL(env.WIDEMIND_OFFSITE_ENDPOINT!);
  if (
    endpoint.username ||
    endpoint.password ||
    endpoint.search ||
    !(
      endpoint.protocol === "https:" ||
      (endpoint.protocol === "http:" &&
        ["127.0.0.1", "localhost", "[::1]"].includes(endpoint.hostname))
    )
  )
    throw new Error(
      "WIDEMIND_OFFSITE_ENDPOINT must be HTTPS (or loopback HTTP) without credentials",
    );
  const bucket = env.WIDEMIND_OFFSITE_BUCKET!;
  if (!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket))
    throw new Error("WIDEMIND_OFFSITE_BUCKET is not a valid bucket name");
  const prefix = env.WIDEMIND_OFFSITE_PREFIX ?? "widemind/";
  if (!/^[A-Za-z0-9._/-]{0,200}$/.test(prefix))
    throw new Error("WIDEMIND_OFFSITE_PREFIX may use letters, digits, . _ / -");
  const publicKeyPem = readFileSync(env.WIDEMIND_OFFSITE_PUBLIC_KEY!, "utf8");
  if (!publicKeyPem.includes("BEGIN PUBLIC KEY"))
    throw new Error(
      "WIDEMIND_OFFSITE_PUBLIC_KEY must point to a PEM public key (npm run backup:keygen)",
    );
  return {
    endpoint: endpoint.origin + endpoint.pathname.replace(/\/+$/, ""),
    bucket,
    region: env.WIDEMIND_OFFSITE_REGION ?? "auto",
    accessKeyId: env.WIDEMIND_OFFSITE_ACCESS_KEY_ID!,
    secretAccessKey: env.WIDEMIND_OFFSITE_SECRET_ACCESS_KEY!,
    prefix,
    publicKeyPem,
  };
}

const sha256 = (data: Buffer | string) =>
  createHash("sha256").update(data).digest("hex");
const hmac = (key: Buffer | string, data: string) =>
  createHmac("sha256", key).update(data).digest();
const encodeSegment = (segment: string) =>
  encodeURIComponent(segment).replace(
    /[!'()*]/g,
    (char) => "%" + char.charCodeAt(0).toString(16).toUpperCase(),
  );

/** AWS Signature Version 4 Authorization header for one request. */
export function signV4(input: {
  method: string;
  url: URL;
  headers: Record<string, string>;
  payloadHash: string;
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
  service: string;
  amzDate: string;
}): string {
  const date = input.amzDate.slice(0, 8);
  const headers = Object.entries(input.headers)
    .map(([name, value]) => [
      name.toLowerCase(),
      value.trim().replace(/\s+/g, " "),
    ])
    .sort(([a], [b]) => (a! < b! ? -1 : 1));
  const signedHeaders = headers.map(([name]) => name).join(";");
  const query = [...input.url.searchParams]
    .map(([key, value]) => [encodeSegment(key), encodeSegment(value)])
    .sort(([a, x], [b, y]) => (a === b ? (x! < y! ? -1 : 1) : a! < b! ? -1 : 1))
    .map(([key, value]) => `${key}=${value}`)
    .join("&");
  const canonical = [
    input.method,
    input.url.pathname
      .split("/")
      .map((segment) => encodeSegment(decodeURIComponent(segment)))
      .join("/"),
    query,
    headers.map(([name, value]) => `${name}:${value}\n`).join(""),
    signedHeaders,
    input.payloadHash,
  ].join("\n");
  const scope = `${date}/${input.region}/${input.service}/aws4_request`;
  const toSign = [
    "AWS4-HMAC-SHA256",
    input.amzDate,
    scope,
    sha256(canonical),
  ].join("\n");
  const key = hmac(
    hmac(
      hmac(hmac("AWS4" + input.secretAccessKey, date), input.region),
      input.service,
    ),
    "aws4_request",
  );
  const signature = createHmac("sha256", key).update(toSign).digest("hex");
  return `AWS4-HMAC-SHA256 Credential=${input.accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
}

export function generateBackupKeys(): {
  publicKeyPem: string;
  privateKeyPem: string;
} {
  const { publicKey, privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 4096,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
  return { publicKeyPem: publicKey, privateKeyPem: privateKey };
}

/** Bundle and encrypt one complete backup directory. */
export function encryptBackup(
  backupPath: string,
  publicKeyPem: string,
): Buffer {
  const names = readdirSync(backupPath)
    .filter((name) => name === "manifest.json" || name.endsWith(".sqlite"))
    .sort();
  if (!names.includes("manifest.json"))
    throw new Error(
      "Only complete backups (with a manifest) can be sent off-host",
    );
  const contents = names.map((name) => readFileSync(join(backupPath, name)));
  const index = Buffer.from(
    JSON.stringify(
      names.map((name, i) => ({ name, bytes: contents[i]!.length })),
    ),
  );
  const length = Buffer.alloc(4);
  length.writeUInt32BE(index.length);
  const plain = Buffer.concat([length, index, ...contents]);
  if (plain.length > MAX_ARCHIVE)
    throw new Error("Backup is larger than the 1 GiB off-host archive limit");
  const key = randomBytes(32),
    iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const header = Buffer.from(
    JSON.stringify({
      version: 1,
      alg: "RSA-OAEP-256+A256GCM",
      backup: basename(backupPath),
      iv: iv.toString("base64"),
      wrappedKey: publicEncrypt(
        {
          key: publicKeyPem,
          padding: cryptoConstants.RSA_PKCS1_OAEP_PADDING,
          oaepHash: "sha256",
        },
        key,
      ).toString("base64"),
    }),
  );
  cipher.setAAD(header);
  const headerLength = Buffer.alloc(4);
  headerLength.writeUInt32BE(header.length);
  const body = Buffer.concat([cipher.update(plain), cipher.final()]);
  return Buffer.concat([
    MAGIC,
    headerLength,
    header,
    body,
    cipher.getAuthTag(),
  ]);
}

/** Decrypt an archive into a new directory. Tampering fails the GCM tag check. */
export function decryptArchive(
  archive: Buffer,
  privateKeyPem: string,
  outDir: string,
): string[] {
  if (!archive.subarray(0, MAGIC.length).equals(MAGIC))
    throw new Error("Not a WideMind backup archive");
  if (existsSync(outDir) && readdirSync(outDir).length)
    throw new Error(`${outDir} is not empty`);
  const headerLength = archive.readUInt32BE(MAGIC.length);
  const start = MAGIC.length + 4;
  const header = archive.subarray(start, start + headerLength);
  const meta = JSON.parse(header.toString("utf8")) as {
    version: number;
    iv: string;
    wrappedKey: string;
  };
  if (meta.version !== 1) throw new Error("Unsupported archive version");
  const key = privateDecrypt(
    {
      key: privateKeyPem,
      padding: cryptoConstants.RSA_PKCS1_OAEP_PADDING,
      oaepHash: "sha256",
    },
    Buffer.from(meta.wrappedKey, "base64"),
  );
  const decipher = createDecipheriv(
    "aes-256-gcm",
    key,
    Buffer.from(meta.iv, "base64"),
  );
  decipher.setAAD(header);
  decipher.setAuthTag(archive.subarray(archive.length - 16));
  const plain = Buffer.concat([
    decipher.update(
      archive.subarray(start + headerLength, archive.length - 16),
    ),
    decipher.final(),
  ]);
  const indexLength = plain.readUInt32BE(0);
  const index = JSON.parse(
    plain.subarray(4, 4 + indexLength).toString("utf8"),
  ) as { name: string; bytes: number }[];
  mkdirSync(outDir, { recursive: true, mode: 0o700 });
  let offset = 4 + indexLength;
  for (const entry of index) {
    const name = basename(entry.name);
    if (name !== entry.name || !/^[a-z0-9._-]+$/i.test(name))
      throw new Error("Archive contains an unsafe file name");
    writeFileSync(
      join(outDir, name),
      plain.subarray(offset, offset + entry.bytes),
      {
        mode: 0o600,
      },
    );
    offset += entry.bytes;
  }
  return index.map((entry) => entry.name);
}

export interface OffsiteMarker {
  createdAt: string;
  uploadedAt: string;
  key: string;
  bytes: number;
  sha256: string;
}

/** Encrypt and upload one complete backup, then record a readable marker. */
export async function uploadBackup(input: {
  settings: OffsiteSettings;
  backup: BackupSettings;
  backupPath: string;
  transport?: typeof fetch;
  now?: number;
}): Promise<OffsiteMarker> {
  const manifest = JSON.parse(
    readFileSync(join(input.backupPath, "manifest.json"), "utf8"),
  ) as Manifest;
  const archive = encryptBackup(input.backupPath, input.settings.publicKeyPem);
  const key = input.settings.prefix + basename(input.backupPath) + ".wmbk";
  const url = new URL(
    `${input.settings.endpoint}/${input.settings.bucket}/${key
      .split("/")
      .map(encodeSegment)
      .join("/")}`,
  );
  const now = input.now ?? Date.now();
  const amzDate = new Date(now)
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}/, "");
  const payloadHash = sha256(archive);
  const headers: Record<string, string> = {
    host: url.host,
    "content-type": "application/octet-stream",
    "x-amz-content-sha256": payloadHash,
    "x-amz-date": amzDate,
  };
  const authorization = signV4({
    method: "PUT",
    url,
    headers,
    payloadHash,
    accessKeyId: input.settings.accessKeyId,
    secretAccessKey: input.settings.secretAccessKey,
    region: input.settings.region,
    service: "s3",
    amzDate,
  });
  const { host: _host, ...sent } = headers;
  const response = await (input.transport ?? fetch)(url, {
    method: "PUT",
    headers: { ...sent, Authorization: authorization },
    body: new Uint8Array(archive),
    signal: AbortSignal.timeout(300_000),
    redirect: "error",
  });
  if (!response.ok)
    throw new Error(
      `Off-host storage refused the upload (HTTP ${response.status})`,
    );
  const marker: OffsiteMarker = {
    createdAt: manifest.createdAt,
    uploadedAt: new Date(now).toISOString(),
    key,
    bytes: archive.length,
    sha256: payloadHash,
  };
  const file = join(input.backup.dir, "offsite.json");
  writeFileSync(file + ".tmp", JSON.stringify(marker) + "\n");
  chmodSync(file + ".tmp", 0o644);
  renameSync(file + ".tmp", file);
  return marker;
}

/** Age of the newest off-host copy; null when off-host backups are not configured. */
export function offsiteFresh(
  configured: boolean,
  backup: BackupSettings | null,
  now = Date.now(),
): { fresh: boolean; detail: string } | null {
  if (!configured) return null;
  if (!backup)
    return { fresh: false, detail: "WIDEMIND_BACKUP_DIR is not set" };
  try {
    const marker = JSON.parse(
      readFileSync(join(backup.dir, "offsite.json"), "utf8"),
    ) as OffsiteMarker;
    const age = now - Date.parse(marker.createdAt);
    if (!Number.isFinite(age)) throw new Error("bad marker");
    const hours = Math.floor(age / 3_600_000);
    return age <= backup.maxAgeMs
      ? {
          fresh: true,
          detail: `Newest off-host copy is from a backup ${hours} hour(s) old`,
        }
      : {
          fresh: false,
          detail: `Newest off-host copy is from a backup ${hours} hour(s) old, over the ${backup.maxAgeMs / 3_600_000} hour limit`,
        };
  } catch {
    return { fresh: false, detail: "No off-host copy has been uploaded yet" };
  }
}
