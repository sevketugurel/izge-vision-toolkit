import {
  readFileSync,
  existsSync,
  readdirSync,
  mkdirSync,
  copyFileSync,
} from "node:fs";
import { resolve, basename } from "node:path";
const root = process.cwd().endsWith("/frontend")
  ? process.cwd()
  : resolve("frontend");
const lock = JSON.parse(readFileSync(resolve(root, "package-lock.json")));
const out = resolve(root, "licenses");
mkdirSync(out, { recursive: true });
for (const [key] of Object.entries(lock.packages)) {
  if (!key || !existsSync(resolve(root, key))) continue;
  for (const file of readdirSync(resolve(root, key))) {
    if (/^(licen[cs]e|copying|notice)(\.|$)/i.test(file)) {
      const target = resolve(out, key.replaceAll("/", "_"));
      mkdirSync(target, { recursive: true });
      try {
        copyFileSync(resolve(root, key, file), resolve(target, basename(file)));
      } catch {}
    }
  }
}
console.log("Collected installed frontend license notices:", out);
