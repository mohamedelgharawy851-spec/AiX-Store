import fs from "node:fs";
import path from "node:path";

function parseValue(raw) {
  const value = raw.trim();
  if (!value) {
    return "";
  }
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    const inner = value.slice(1, -1);
    if (value.startsWith('"')) {
      return inner
        .replace(/\\n/g, "\n")
        .replace(/\\r/g, "\r")
        .replace(/\\t/g, "\t")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, "\\");
    }
    return inner;
  }
  const commentIndex = value.indexOf(" #");
  return commentIndex >= 0 ? value.slice(0, commentIndex).trimEnd() : value;
}

function parseEnvFile(filePath) {
  const entries = new Map();
  const source = fs.readFileSync(filePath, "utf8");
  for (const rawLine of source.split(/\r?\n/)) {
    let line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    if (line.startsWith("export ")) {
      line = line.slice(7).trimStart();
    }
    const separatorIndex = line.indexOf("=");
    if (separatorIndex <= 0) {
      continue;
    }
    const key = line.slice(0, separatorIndex).trim();
    if (!key || /\s/.test(key)) {
      continue;
    }
    entries.set(key, parseValue(line.slice(separatorIndex + 1)));
  }
  return entries;
}

export function loadShopEaseEnv(rootDir, env = process.env) {
  const candidates = [path.join(rootDir, ".env"), path.join(rootDir, ".env.local")];
  const loaded = new Map();
  const loadedFiles = [];
  for (const filePath of candidates) {
    if (!fs.existsSync(filePath)) {
      continue;
    }
    for (const [key, value] of parseEnvFile(filePath).entries()) {
      loaded.set(key, value);
    }
    loadedFiles.push(filePath);
  }
  for (const [key, value] of loaded.entries()) {
    if (!(key in env)) {
      env[key] = value;
    }
  }
  return loadedFiles;
}
