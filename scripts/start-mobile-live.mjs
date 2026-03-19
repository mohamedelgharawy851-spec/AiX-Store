import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";
import { loadAIXStoreEnv } from "./load-env.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "..");
loadAIXStoreEnv(rootDir);
const mode = process.argv[2] || "start";
const runtimePort = Number(process.env.AIXSTORE_RUNTIME_PORT || 8787);
const pythonPort = Number(process.env.AIXSTORE_PYTHON_PORT || 8790);

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function resolveAdbBinary() {
  const sdkRoot = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
  if (sdkRoot) {
    const adbPath = path.join(sdkRoot, "platform-tools", "adb");
    if (fs.existsSync(adbPath)) {
      return adbPath;
    }
  }

  return "adb";
}

function ensureAndroidReversePort() {
  const adbBinary = resolveAdbBinary();
  const result = spawnSync(adbBinary, ["reverse", `tcp:${runtimePort}`, `tcp:${runtimePort}`], {
    encoding: "utf8",
    env: process.env,
    stdio: "pipe",
    timeout: 10_000,
  });

  if (result.status !== 0) {
    const detail = result.stderr?.trim() || result.stdout?.trim() || "unknown adb error";
    console.warn(`Could not enable adb reverse for port ${runtimePort}: ${detail}`);
    return false;
  }

  console.log(`adb reverse enabled for tcp:${runtimePort}.`);
  return true;
}

function resolveLanHost() {
  const interfaces = os.networkInterfaces();
  for (const entries of Object.values(interfaces)) {
    for (const entry of entries || []) {
      if (entry && entry.family === "IPv4" && !entry.internal) {
        return entry.address;
      }
    }
  }
  return "";
}

function findBundledChromium() {
  const homeDir = process.env.HOME;
  if (!homeDir) {
    return "";
  }

  const playwrightCacheDir = path.join(homeDir, ".cache", "ms-playwright");
  if (!fs.existsSync(playwrightCacheDir)) {
    return "";
  }

  const directories = fs
    .readdirSync(playwrightCacheDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
    .reverse();

  for (const directory of directories) {
    for (const relativePath of ["chrome-linux64/chrome", "chrome-linux/chrome"]) {
      const executablePath = path.join(playwrightCacheDir, directory, relativePath);
      if (fs.existsSync(executablePath)) {
        return executablePath;
      }
    }
  }

  return "";
}

function requestHealth(port) {
  return new Promise((resolve) => {
    const request = http.get(`http://127.0.0.1:${port}/health`, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("error", () => resolve(false));
  });
}

function resolvePythonBinary() {
  const venvPython = path.join(rootDir, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

async function ensurePythonRuntime() {
  if (await requestHealth(pythonPort)) {
    return null;
  }

  const pythonProcess = spawn(
    resolvePythonBinary(),
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(pythonPort)],
    {
      cwd: path.join(rootDir, "services/scraper-python"),
      stdio: "inherit",
      env: process.env,
    },
  );

  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (await requestHealth(pythonPort)) {
      return pythonProcess;
    }
    await wait(500);
  }

  throw new Error(`Python scraper service did not become healthy on port ${pythonPort}.`);
}

async function ensureNodeRuntime() {
  if (await requestHealth(runtimePort)) {
    return null;
  }

  const runtimeProcess = spawn(
    process.execPath,
    [path.join(rootDir, "services/scraper/server.mjs")],
    {
      cwd: rootDir,
      stdio: "inherit",
      env: {
        ...process.env,
        AIXSTORE_PYTHON_PORT: String(pythonPort),
      },
    },
  );

  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (await requestHealth(runtimePort)) {
      return runtimeProcess;
    }
    await wait(500);
  }

  throw new Error(`Catalog runtime did not become healthy on port ${runtimePort}.`);
}

async function main() {
  const pythonProcess = await ensurePythonRuntime();
  const runtimeProcess = await ensureNodeRuntime();
  const expoEnv = { ...process.env };

  if (!expoEnv.EDGE_PATH) {
    const chromiumPath = findBundledChromium();
    if (chromiumPath) {
      expoEnv.EDGE_PATH = chromiumPath;
    }
  }

  const expoArgs = [path.join(rootDir, "node_modules/expo/bin/cli"), "start"];
  if (mode === "android") {
    const reverseEnabled = ensureAndroidReversePort();
    if (reverseEnabled) {
      expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL = `http://127.0.0.1:${runtimePort}`;
    } else {
      const lanHost = resolveLanHost();
      if (lanHost) {
        expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL = `http://${lanHost}:${runtimePort}`;
        console.warn(
          `Using LAN runtime URL ${expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL} because adb reverse is unavailable.`,
        );
      } else {
        console.warn("adb reverse is unavailable and no LAN IPv4 address was found.");
      }
    }
    expoArgs.push("--android");
  } else if (mode === "web") {
    expoArgs.push("--web");
  }

  const expoProcess = spawn(process.execPath, expoArgs, {
    cwd: path.join(rootDir, "apps/mobile"),
    stdio: "inherit",
    env: expoEnv,
  });

  expoProcess.on("exit", (code) => {
    if (runtimeProcess) {
      runtimeProcess.kill("SIGTERM");
    }
    if (pythonProcess) {
      pythonProcess.kill("SIGTERM");
    }
    process.exit(code ?? 0);
  });
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
