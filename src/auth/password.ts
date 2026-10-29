import {
  randomBytes,
  scrypt as scryptCallback,
  timingSafeEqual,
} from "node:crypto";
import { promisify } from "node:util";
const scrypt = promisify(scryptCallback);
export async function hashPassword(password: string) {
  if (Buffer.byteLength(password) < 12 || Buffer.byteLength(password) > 1024)
    throw new Error("Password must contain 12–1024 UTF-8 bytes");
  const salt = randomBytes(16).toString("hex");
  const key = (await scrypt(password, salt, 32)) as Buffer;
  return `scrypt:1:${salt}:${key.toString("hex")}`;
}
export function validPasswordHash(hash: string) {
  return /^scrypt:1:[a-f0-9]{32}:[a-f0-9]{64}$/.test(hash);
}
export async function verifyPassword(password: string, hash: string) {
  if (!validPasswordHash(hash) || Buffer.byteLength(password) > 1024)
    return false;
  const [, , salt, key] = hash.split(":");
  const derived = (await scrypt(password, salt!, 32)) as Buffer;
  return timingSafeEqual(derived, Buffer.from(key!, "hex"));
}
