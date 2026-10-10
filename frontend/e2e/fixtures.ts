import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { APIRequestContext } from "@playwright/test";

/** Synthetic H.264 MP4 generated at test time; no video file is committed. */
export function syntheticVideo(name = "synthetic.mp4", seconds = 6, size = "320x180"): string {
  const path = join(mkdtempSync(join(tmpdir(), "izge-e2e-")), name);
  execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
    `testsrc2=size=${size}:rate=10:duration=${seconds}`, "-c:v", "libx264",
    "-pix_fmt", "yuv420p", path]);
  return path;
}

export async function post(api: APIRequestContext, path: string, data: unknown) {
  const response = await api.post(path, { data });
  if (!response.ok()) throw new Error(`${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
