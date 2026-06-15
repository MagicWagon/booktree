import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");
const workerPath = path.join(root, "booktree_worker.py");

export function runWorker(args) {
  return new Promise((resolve, reject) => {
    const python = process.env.BOOKTREE_PYTHON || "python3";
    const child = spawn(python, [workerPath, ...args], {
      cwd: root,
      env: {
        ...process.env,
        BOOKTREE_DB: process.env.BOOKTREE_DB || "/config/booktree.db",
        BOOKTREE_CONFIG: process.env.BOOKTREE_CONFIG || "/config/config.json",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      const trimmed = stdout.trim();
      let payload = {};
      try {
        payload = trimmed ? JSON.parse(trimmed.split("\n").at(-1)) : {};
      } catch (error) {
        reject(new Error(`Worker returned invalid JSON: ${error.message}\n${stdout}\n${stderr}`));
        return;
      }

      if (code !== 0 || payload.ok === false) {
        reject(new Error(payload.error || stderr || `Worker exited with code ${code}`));
        return;
      }
      resolve(payload);
    });
  });
}

export function jsonError(error, status = 500) {
  return Response.json({ ok: false, error: error.message || String(error) }, { status });
}
