import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { join } from "node:path";
import { runBackup, verifyBackup } from "../src/runtime/backup.ts";
import {
  decryptArchive,
  encryptBackup,
  generateBackupKeys,
  offsiteFresh,
  offsiteSettings,
  signV4,
  uploadBackup,
} from "../src/runtime/offsite.ts";
import { alertPass } from "../src/runtime/alerts.ts";
import { fixture } from "./helpers.ts";

const keys = generateBackupKeys();
const other = generateBackupKeys();

test("Signature V4 matches the published AWS S3 GET example", () => {
  const empty = createHash("sha256").update("").digest("hex");
  const authorization = signV4({
    method: "GET",
    url: new URL("https://examplebucket.s3.amazonaws.com/test.txt"),
    headers: {
      host: "examplebucket.s3.amazonaws.com",
      range: "bytes=0-9",
      "x-amz-content-sha256": empty,
      "x-amz-date": "20130524T000000Z",
    },
    payloadHash: empty,
    accessKeyId: "AKIAIOSFODNN7EXAMPLE",
    secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    region: "us-east-1",
    service: "s3",
    amzDate: "20130524T000000Z",
  });
  assert.equal(
    authorization,
    "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
  );
});

test("settings require every value and a safe endpoint", () => {
  const f = fixture();
  try {
    const publicKey = join(f.dir, "public.pem");
    writeFileSync(publicKey, keys.publicKeyPem);
    assert.equal(offsiteSettings({}), null);
    assert.throws(
      () =>
        offsiteSettings({
          WIDEMIND_OFFSITE_ENDPOINT: "https://s3.example.com",
        }),
      /WIDEMIND_OFFSITE_BUCKET, WIDEMIND_OFFSITE_ACCESS_KEY_ID/,
    );
    const env = {
      WIDEMIND_OFFSITE_ENDPOINT: "https://s3.example.com/",
      WIDEMIND_OFFSITE_BUCKET: "widemind-backups",
      WIDEMIND_OFFSITE_ACCESS_KEY_ID: "id",
      WIDEMIND_OFFSITE_SECRET_ACCESS_KEY: "secret",
      WIDEMIND_OFFSITE_PUBLIC_KEY: publicKey,
    };
    assert.equal(offsiteSettings(env)?.endpoint, "https://s3.example.com");
    assert.throws(
      () =>
        offsiteSettings({
          ...env,
          WIDEMIND_OFFSITE_ENDPOINT: "http://s3.example.com",
        }),
      /HTTPS/,
    );
    assert.throws(
      () => offsiteSettings({ ...env, WIDEMIND_OFFSITE_BUCKET: "Bad_Bucket" }),
      /bucket/,
    );
    writeFileSync(join(f.dir, "private.pem"), keys.privateKeyPem);
    assert.throws(
      () =>
        offsiteSettings({
          ...env,
          WIDEMIND_OFFSITE_PUBLIC_KEY: join(f.dir, "private.pem"),
        }),
      /public key/,
    );
  } finally {
    f.close();
  }
});

test("archives decrypt only with the private key and detect tampering", async () => {
  const f = fixture();
  try {
    f.store.create("demo");
    const settings = {
      dir: join(f.dir, "backups"),
      keep: 3,
      maxAgeMs: 3_600_000,
    };
    const backup = await runBackup({
      settings,
      sources: [{ label: "runtime", path: f.config.database }],
    });
    const archive = encryptBackup(backup.path, keys.publicKeyPem);
    assert.equal(
      archive.includes(Buffer.from("runtime_runs")),
      false,
      "archive is not plaintext",
    );
    const out = join(f.dir, "restored");
    assert.deepEqual(decryptArchive(archive, keys.privateKeyPem, out), [
      "manifest.json",
      "runtime.sqlite",
    ]);
    assert.equal(verifyBackup(out).ok, true);
    assert.throws(
      () => decryptArchive(archive, keys.privateKeyPem, out),
      /not empty/,
    );
    assert.throws(() =>
      decryptArchive(archive, other.privateKeyPem, join(f.dir, "x")),
    );
    const tampered = Buffer.from(archive);
    tampered[tampered.length - 40] = tampered[tampered.length - 40]! ^ 1;
    assert.throws(() =>
      decryptArchive(tampered, keys.privateKeyPem, join(f.dir, "y")),
    );
    assert.equal(existsSync(join(f.dir, "y", "runtime.sqlite")), false);
  } finally {
    f.close();
  }
});

test("uploads are signed, recorded only on success, and drive freshness alerts", async () => {
  const f = fixture();
  const objects = new Map<string, Buffer>();
  let refuse = false;
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    const body = Buffer.concat(chunks);
    const headers = {
      host: req.headers.host!,
      "content-type": String(req.headers["content-type"]),
      "x-amz-content-sha256": String(req.headers["x-amz-content-sha256"]),
      "x-amz-date": String(req.headers["x-amz-date"]),
    };
    const expected = signV4({
      method: req.method!,
      url: new URL(req.url!, "http://" + req.headers.host),
      headers,
      payloadHash: headers["x-amz-content-sha256"],
      accessKeyId: "key-id",
      secretAccessKey: "key-secret",
      region: "auto",
      service: "s3",
      amzDate: headers["x-amz-date"],
    });
    const hashOk =
      createHash("sha256").update(body).digest("hex") ===
      headers["x-amz-content-sha256"];
    if (refuse || req.headers.authorization !== expected || !hashOk) {
      res.writeHead(403).end();
      return;
    }
    objects.set(req.url!, body);
    res.writeHead(200).end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const id = f.store.create("demo");
    mkdirSync(join(f.dir, "keys"));
    writeFileSync(join(f.dir, "keys", "public.pem"), keys.publicKeyPem);
    const settings = offsiteSettings({
      WIDEMIND_OFFSITE_ENDPOINT: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
      WIDEMIND_OFFSITE_BUCKET: "widemind-backups",
      WIDEMIND_OFFSITE_ACCESS_KEY_ID: "key-id",
      WIDEMIND_OFFSITE_SECRET_ACCESS_KEY: "key-secret",
      WIDEMIND_OFFSITE_PUBLIC_KEY: join(f.dir, "keys", "public.pem"),
    })!;
    const backupSettings = {
      dir: join(f.dir, "backups"),
      keep: 3,
      maxAgeMs: 3_600_000,
    };
    const t = Date.parse("2026-11-04T10:00:00Z");
    const backup = await runBackup({
      settings: backupSettings,
      sources: [{ label: "runtime", path: f.config.database }],
      now: t,
    });
    assert.equal(offsiteFresh(true, backupSettings, t)?.fresh, false);
    assert.equal(offsiteFresh(false, backupSettings, t), null);

    refuse = true;
    await assert.rejects(
      uploadBackup({
        settings,
        backup: backupSettings,
        backupPath: backup.path,
        now: t,
      }),
      /HTTP 403/,
    );
    assert.equal(existsSync(join(backupSettings.dir, "offsite.json")), false);

    refuse = false;
    const marker = await uploadBackup({
      settings,
      backup: backupSettings,
      backupPath: backup.path,
      now: t,
    });
    assert.equal(
      marker.key,
      "widemind/" + backup.path.split("/").at(-1) + ".wmbk",
    );
    const stored = objects.get("/widemind-backups/" + marker.key)!;
    assert.equal(
      createHash("sha256").update(stored).digest("hex"),
      marker.sha256,
    );
    const out = join(f.dir, "from-bucket");
    decryptArchive(stored, keys.privateKeyPem, out);
    assert.equal(verifyBackup(out).ok, true);
    const text = readFileSync(join(backupSettings.dir, "offsite.json"), "utf8");
    assert.equal(text.includes("key-secret"), false);
    assert.equal(offsiteFresh(true, backupSettings, t + 1000)?.fresh, true);

    const alerts = await alertPass({
      db: f.db,
      services: f.services,
      config: f.config,
      runId: id,
      settings: {
        webhookUrl: null,
        repeatMs: 3_600_000,
        economy: null,
        backup: backupSettings,
        offsiteConfigured: true,
      },
      now: t + 2 * 3_600_000,
    });
    assert.ok(alerts.active.includes("offsite_backup_stale"));
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
