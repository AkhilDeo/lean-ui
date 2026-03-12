const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function detectPackageName() {
  const parts = [process.platform, process.arch];
  if (process.platform === "linux") {
    const { MUSL, familySync } = require("detect-libc");
    const family = familySync();
    if (family === MUSL) {
      parts.push("musl");
    } else if (process.arch === "arm") {
      parts.push("gnueabihf");
    } else {
      parts.push("gnu");
    }
  } else if (process.platform === "win32") {
    parts.push("msvc");
  }
  return `lightningcss-${parts.join("-")}`;
}

function installPackage(packageName, version) {
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const result = spawnSync(
    npmCommand,
    ["install", "--no-save", "--ignore-scripts", `${packageName}@${version}`],
    {
      cwd: path.resolve(__dirname, ".."),
      stdio: "inherit",
      env: process.env,
    }
  );
  if (result.status !== 0) {
    throw new Error(`Failed to install ${packageName}@${version}`);
  }
}

function main() {
  try {
    require("lightningcss");
    return;
  } catch (error) {
    const message = String(error && error.message ? error.message : error);
    if (!message.includes("lightningcss")) {
      throw error;
    }
  }

  const packageJsonPath = path.resolve(
    __dirname,
    "..",
    "node_modules",
    "lightningcss",
    "package.json"
  );
  if (!fs.existsSync(packageJsonPath)) {
    throw new Error("node_modules/lightningcss/package.json is missing");
  }

  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
  const version = packageJson.version;
  const packageName = detectPackageName();

  installPackage(packageName, version);
  require("lightningcss");
}

main();
