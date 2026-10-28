import { hashPassword } from "./password.ts";
let password = "";
for await (const part of process.stdin) {
  password += part;
  if (Buffer.byteLength(password) > 1024) throw new Error("Password too long");
}
console.log(await hashPassword(password.replace(/\r?\n$/, "")));
