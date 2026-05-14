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
const runtimePort = Number(process.env.AIXSTORE_RUNTIME_PORT || 7860);
const pythonPort = Number(process.env.AIXSTORE_PYTHON_PORT || 8790);
const metroPort = Number(
  process.env.AIXSTORE_METRO_PORT || process.env.RCT_METRO_PORT || process.env.EXPO_METRO_PORT || 8081,
);

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function resolveAdbBinary() {
  const sdkRoot = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
  if (sdkRoot) {
    const names = process.platform === "win32" ? ["adb.exe", "adb"] : ["adb"];
    for (const name of names) {
      const adbPath = path.join(sdkRoot, "platform-tools", name);
      if (fs.existsSync(adbPath)) {
        return adbPath;
      }
    }
  }

  return process.platform === "win32" ? "adb.exe" : "adb";
}

function adbHasAuthorizedDevice() {
  const adbBinary = resolveAdbBinary();
  const result = spawnSync(adbBinary, ["devices"], {
    encoding: "utf8",
    env: process.env,
    stdio: "pipe",
    timeout: 15_000,
  });
  if (result.status !== 0) {
    return false;
  }
  const text = result.stdout || "";
  for (const line of text.split(/\r?\n/)) {
    if (/^\S+\s+device\s*$/.test(line.trim())) {
      return true;
    }
  }
  return false;
}

/** Wait until `adb devices` shows at least one line in `device` state (not `offline` / `unauthorized`). */
async function waitForAdbDeviceOrTimeout() {
  const maxMs = Number(process.env.AIXSTORE_ADB_WAIT_MS || 240_000);
  const start = Date.now();
  if (adbHasAuthorizedDevice()) {
    await waitForAndroidBootCompleted();
    return;
  }
  console.log(
    `Waiting for an Android device or emulator (up to ${Math.round(maxMs / 1000)}s). Open Android Studio → Device Manager → run your AVD, then wait…`,
  );
  while (Date.now() - start < maxMs) {
    await wait(3000);
    if (adbHasAuthorizedDevice()) {
      console.log("Android device is ready (adb `device` state).");
      await waitForAndroidBootCompleted();
      return;
    }
  }
  console.warn("Timed out waiting for adb `device` — start an emulator and try again, or use `npm run mobile` for Expo Go + QR.");
}

async function waitForAndroidBootCompleted() {
  const adbBinary = resolveAdbBinary();
  const maxMs = Number(process.env.AIXSTORE_ADB_BOOT_WAIT_MS || 120_000);
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const result = spawnSync(adbBinary, ["shell", "getprop", "sys.boot_completed"], {
      encoding: "utf8",
      env: process.env,
      stdio: "pipe",
      timeout: 20_000,
    });
    if (result.status === 0 && String(result.stdout || "").trim() === "1") {
      console.log("Android system boot finished (sys.boot_completed=1).");
      return;
    }
    await wait(2500);
  }
  console.warn("sys.boot_completed did not become 1 in time — continuing anyway (Expo may still work).");
}

function adbReverseTcpPort(port) {
  const adbBinary = resolveAdbBinary();
  const result = spawnSync(adbBinary, ["reverse", `tcp:${port}`, `tcp:${port}`], {
    encoding: "utf8",
    env: process.env,
    stdio: "pipe",
    timeout: 10_000,
  });

  if (result.status !== 0) {
    const detail = result.stderr?.trim() || result.stdout?.trim() || "unknown adb error";
    console.warn(`Could not enable adb reverse for tcp:${port}: ${detail}`);
    return false;
  }

  console.log(`adb reverse enabled for tcp:${port}.`);
  return true;
}

/** USB (or emulator): reverse Metro + catalog runtime so the device can use 127.0.0.1. */
function ensureAndroidUsbPortForwarding() {
  const metroOk = adbReverseTcpPort(metroPort);
  const runtimeOk = adbReverseTcpPort(runtimePort);
  return { metroOk, runtimeOk };
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
  const candidates = [
    path.join(rootDir, ".venv", "bin", "python"),
    path.join(rootDir, ".venv", "Scripts", "python.exe"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return process.platform === "win32" ? "python" : "python3";
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

  if (mode === "start") {
    // --offline skips api.expo.dev fetches that often cause "TypeError: fetch failed" on Windows
    // (VPN/proxy/TLS). Cannot combine --offline with --lan; default dev hosting still works on LAN.
    expoArgs.push("--go", "--offline");
    const lanHost = resolveLanHost();
    if (!String(expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL || "").trim() && lanHost) {
      expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL = `http://${lanHost}:${runtimePort}`;
    }
    if (lanHost && expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL) {
      console.log(
        `Expo Go: scan the QR code below. Phone and PC should be on the same Wi‑Fi. Catalog runtime → ${expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL}`,
      );
    } else {
      console.log("Expo Go: scan the QR code below when it appears. Use the same Wi‑Fi as this PC if the app cannot reach the API.");
    }
  } else if (mode === "android") {
    await waitForAdbDeviceOrTimeout();
    const hasDevice = adbHasAuthorizedDevice();
    if (!hasDevice) {
      console.warn(
        "No Android device or emulator is connected (adb shows nothing in 'device' state). " +
          "Skipping Expo --android so Metro can still start. Connect USB or start an AVD, run `adb devices`, then press `a` in Expo or rerun this command.",
      );
    }
    const { metroOk, runtimeOk } = hasDevice ? ensureAndroidUsbPortForwarding() : { metroOk: false, runtimeOk: false };
    if (runtimeOk) {
      expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL = `http://127.0.0.1:${runtimePort}`;
    } else {
      const lanHost = resolveLanHost();
      if (lanHost) {
        expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL = `http://${lanHost}:${runtimePort}`;
        console.warn(
          `Using LAN runtime URL ${expoEnv.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL} because adb reverse for the catalog runtime failed.`,
        );
      } else {
        console.warn("adb reverse for the catalog runtime failed and no LAN IPv4 address was found.");
      }
    }
    if (metroOk) {
      expoEnv.REACT_NATIVE_PACKAGER_HOSTNAME = "127.0.0.1";
      expoEnv.RCT_METRO_PORT = String(metroPort);
    } else if (hasDevice) {
      console.warn(
        `adb reverse for Metro (tcp:${metroPort}) failed; use the same Wi‑Fi as this PC for Expo Go, or fix USB debugging.`,
      );
    } else {
      console.warn(
        `Connect an Android device (USB debugging on) or start an emulator, then run \`adb devices\` — use Expo Go on the same Wi‑Fi as this PC for Metro (port ${metroPort}).`,
      );
    }
    if (hasDevice) {
      expoArgs.push("--android");
    } else {
      expoArgs.push("--go", "--offline");
    }
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
