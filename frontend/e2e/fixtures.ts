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

/** Two-stall synthetic video matching backend/tests/synthetic.py (red = yemde, blue = yemde değil). */
export function stallVideo(name = "SENTETIK_20261010_143000.mp4", seconds = 90): string {
  const red = "255*(lt(X,160)*lt(T,40)+gte(X,160)*(gte(T,20)*lt(T,60)+gte(T,65)*lt(T,80)))";
  const blue = "255*(lt(X,160)*gte(T,40)*lt(T,70)+gte(X,160)*(lt(T,20)+gte(T,60)*lt(T,65)))";
  const path = join(mkdtempSync(join(tmpdir(), "izge-e2e-")), name);
  execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
    `color=c=black:s=320x180:r=10:d=${seconds}`, "-vf", `format=rgb24,geq=r='${red}':g='0':b='${blue}'`,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", path]);
  return path;
}

export async function waitForRun(api: APIRequestContext, base: string, runId: string, timeout = 120_000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const run = await (await api.get(`${base}/runs/${runId}`)).json();
    if (run.status === "succeeded") return run;
    if (run.status === "failed") throw new Error(`run failed: ${JSON.stringify(run.job)}`);
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("run did not finish");
}
