"use strict";

const { createHash } = require("crypto");
const {
  chmodSync,
  closeSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  readSync,
  renameSync,
  rmSync,
  writeSync,
} = require("fs");
const { dirname, isAbsolute, join, normalize, relative, resolve, sep } = require("path");

function copyTreeSync(source, destination) {
  const metadata = lstatSync(source);
  if (metadata.isDirectory()) {
    mkdirSync(destination, { recursive: true });
    for (const name of readdirSync(source)) {
      copyTreeSync(join(source, name), join(destination, name));
    }
    return;
  }
  if (!metadata.isFile()) {
    throw new Error(`native payload is not a regular file: ${source}`);
  }
  mkdirSync(dirname(destination), { recursive: true });
  copyFileSync(source, destination);
}

function digest(path) {
  const hash = createHash("sha256");
  const fd = openSync(path, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let count;
    while ((count = readSync(fd, buffer, 0, buffer.length, null)) > 0) hash.update(buffer.subarray(0, count));
  } finally {
    closeSync(fd);
  }
  return hash.digest("hex");
}

function checkedPayloadPath(root, payloadPath) {
  if (!/^(bin|lib|parts)\/[a-zA-Z0-9_./-]+$/.test(payloadPath) ||
      payloadPath.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error(`invalid native payload path: ${payloadPath}`);
  }
  if (isAbsolute(payloadPath)) {
    throw new Error(`native checksum contains an absolute path: ${payloadPath}`);
  }
  const normalized = normalize(payloadPath);
  if (normalized === ".." || normalized.startsWith(`..${sep}`)) {
    throw new Error(`native checksum escapes the package: ${payloadPath}`);
  }
  const canonicalRoot = resolve(root);
  const resolved = resolve(canonicalRoot, normalized);
  const relativePath = relative(canonicalRoot, resolved);
  if (relativePath === ".." || relativePath.startsWith(`..${sep}`) || isAbsolute(relativePath)) {
    throw new Error(`native checksum escapes the package: ${payloadPath}`);
  }
  return resolved;
}

function listPayloadFiles(root, directory = root) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...listPayloadFiles(root, path));
    } else {
      files.push(relative(root, path).split(sep).join("/"));
    }
  }
  return files;
}

function verifyPackageIdentity(root, name, expectedVersion) {
  const packageJson = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  if (packageJson.name !== name || packageJson.version !== expectedVersion) {
    throw new Error(
      `native package mismatch: expected ${name}@${expectedVersion}, got ${packageJson.name}@${packageJson.version}`,
    );
  }
  return packageJson;
}

function verifyPayload(platformRoot, entry, expectedVersion) {
  const packageJson = verifyPackageIdentity(platformRoot, entry.package, expectedVersion);
  const checksumPath = join(platformRoot, "checksums.json");
  const checksums = JSON.parse(readFileSync(checksumPath, "utf8"));
  if (![1, 2].includes(checksums.schemaVersion) || checksums.target !== entry.target) {
    throw new Error(`invalid native checksum manifest for ${entry.package}`);
  }
  const expectedBinary = `bin/${entry.binary}`;
  if (!checksums.files || !checksums.files[expectedBinary]) {
    throw new Error(`native checksum manifest does not contain ${expectedBinary}`);
  }

  const components = checksums.schemaVersion === 2 ? entry.components ?? [] : [];
  if (checksums.schemaVersion === 2 && (!components.length ||
      JSON.stringify(checksums.components) !== JSON.stringify(components.map((component) => component.package)) ||
      Object.keys(packageJson.dependencies ?? {}).length !== components.length ||
      components.some((component) => packageJson.dependencies?.[component.package] !== expectedVersion))) {
    throw new Error("native component dependencies do not match the platform manifest");
  }
  const externalFiles = new Set(components.map((component) => component.file));
  for (const file of externalFiles) {
    if (!checksums.files[file]) throw new Error(`native checksum manifest does not contain ${file}`);
  }
  // Validate the complete installed manifest, including files reconstructed later.
  for (const [file, expected] of Object.entries(checksums.files)) {
    checkedPayloadPath(platformRoot, file);
    if (!Number.isSafeInteger(expected.bytes) || expected.bytes < 0 || !/^[a-f0-9]{64}$/.test(expected.sha256)) {
      throw new Error(`invalid native checksum: ${file}`);
    }
  }
  verifyFiles(platformRoot, Object.fromEntries(
    Object.entries(checksums.files).filter(([file]) => !externalFiles.has(file))));
  return checksums;
}

function verifyFiles(root, files) {
  const declaredFiles = Object.keys(files).sort();
  const actualFiles = ["bin", "lib", "parts"]
    .filter((directory) => existsSync(join(root, directory)))
    .flatMap((directory) => listPayloadFiles(root, join(root, directory)))
    .sort();
  if (JSON.stringify(actualFiles) !== JSON.stringify(declaredFiles)) {
    throw new Error("native payload files do not match the checksum manifest");
  }

  for (const [payloadPath, expected] of Object.entries(files)) {
    const absolutePath = checkedPayloadPath(root, payloadPath);
    const metadata = lstatSync(absolutePath);
    if (!metadata.isFile()) {
      throw new Error(`native payload is not a file: ${payloadPath}`);
    }
    if (metadata.size !== expected.bytes || digest(absolutePath) !== expected.sha256) {
      throw new Error(`native payload checksum mismatch: ${payloadPath}`);
    }
  }
}

function materialize({ metaRoot, platformRoot, entry, expectedVersion }) {
  const checksums = verifyPayload(platformRoot, entry, expectedVersion);

  const temporaryRoot = join(metaRoot, `.native-${process.pid}.tmp`);
  const metaBin = join(metaRoot, "bin");
  const sourceBin = join(platformRoot, "bin");
  const sourceLib = join(platformRoot, "lib");
  const stagedBin = join(temporaryRoot, "bin");
  const stagedLib = join(temporaryRoot, "lib");
  rmSync(temporaryRoot, { recursive: true, force: true });
  mkdirSync(temporaryRoot, { recursive: true });

  try {
    const nativeEntries = readdirSync(sourceBin);
    if (nativeEntries.length !== 1 || nativeEntries[0] !== entry.binary) {
      throw new Error("native package bin/ must contain only the zg executable");
    }
    mkdirSync(stagedBin, { recursive: true });
    copyFileSync(join(sourceBin, entry.binary), join(stagedBin, entry.binary));
    copyTreeSync(sourceLib, stagedLib);

    const components = checksums.schemaVersion === 2 ? entry.components : [];
    for (const file of new Set(components.map((component) => component.file))) {
      const sources = [];
      for (const component of components.filter((candidate) => candidate.file === file)) {
        const root = resolvePackageRoot(metaRoot, component.package, platformRoot);
        verifyPackageIdentity(root, component.package, expectedVersion);
        const part = JSON.parse(readFileSync(join(root, "checksums.json"), "utf8"));
        if (part.schemaVersion !== 1 || part.target !== entry.target || part.component !== component.package ||
            JSON.stringify(Object.keys(part.files ?? {})) !== JSON.stringify([component.payload])) {
          throw new Error(`invalid native component manifest: ${component.package}`);
        }
        verifyFiles(root, part.files);
        sources.push(checkedPayloadPath(root, component.payload));
      }
      const destination = checkedPayloadPath(temporaryRoot, file);
      mkdirSync(dirname(destination), { recursive: true });
      // Concatenate in manifest order with bounded memory. Even the complete
      // Linux ORT provider can exceed Node's maximum string/buffer sizes.
      const output = openSync(destination, "wx", lstatSync(sources[0]).mode);
      const buffer = Buffer.allocUnsafe(1024 * 1024);
      try {
        for (const source of sources) {
          const input = openSync(source, "r");
          try {
            let count;
            while ((count = readSync(input, buffer, 0, buffer.length, null)) > 0) {
              let offset = 0;
              while (offset < count) offset += writeSync(output, buffer, offset, count - offset);
            }
          } finally {
            closeSync(input);
          }
        }
      } finally {
        closeSync(output);
      }
    }
    // Verify the original full runtime manifest before touching the previous
    // installation. Missing/swapped/truncated parts cannot become loadable DLLs.
    verifyFiles(temporaryRoot, checksums.files);

    mkdirSync(metaBin, { recursive: true });
    const metaLib = join(metaRoot, "lib");
    rmSync(metaLib, { recursive: true, force: true });
    renameSync(stagedLib, metaLib);

    const finalBinary = join(metaBin, "zg.exe");
    const temporaryBinary = join(metaBin, `.zg-${process.pid}.tmp`);
    copyFileSync(join(stagedBin, entry.binary), temporaryBinary);
    if (process.platform !== "win32") {
      chmodSync(temporaryBinary, 0o755);
    }
    if (process.platform === "win32") {
      rmSync(finalBinary, { force: true });
    }
    renameSync(temporaryBinary, finalBinary);
  } finally {
    rmSync(temporaryRoot, { recursive: true, force: true });
  }
}

function resolvePackageRoot(metaRoot, packageName, platformRoot = metaRoot) {
  try {
    return dirname(require.resolve(`${packageName}/package.json`, { paths: [resolve(platformRoot), resolve(metaRoot)] }));
  } catch (error) {
    const detail = error && error.code === "MODULE_NOT_FOUND" ? "" : ` (${error.message})`;
    throw new Error(
      `missing ${packageName}; reinstall without --omit=optional or install all exact-version platform component tarballs${detail}`,
    );
  }
}

function resolvePlatformRoot(metaRoot, entry) {
  return resolvePackageRoot(metaRoot, entry.package);
}

function selectPlatform(platforms, platform, arch, reportHeader = {}) {
  const libc = platform === "linux"
    ? (reportHeader.glibcVersionRuntime ? "glibc" : "musl")
    : undefined;
  const matches = platforms.filter((entry) =>
    entry.os === platform
    && entry.cpu === arch
    && (entry.libc === undefined || entry.libc === libc));
  if (matches.length !== 1) {
    const suffix = libc ? `-${libc}` : "";
    throw new Error(`zvec-grep does not publish a native package for ${platform}-${arch}${suffix}`);
  }
  return matches[0];
}

function main() {
  const metaRoot = __dirname;
  const packageJson = JSON.parse(readFileSync(join(metaRoot, "package.json"), "utf8"));
  const platforms = JSON.parse(readFileSync(join(metaRoot, "platforms.json"), "utf8"));
  const report = process.report && process.report.getReport();
  const entry = selectPlatform(platforms, process.platform, process.arch, report?.header ?? {});
  const platformRoot = resolvePlatformRoot(metaRoot, entry);
  materialize({
    metaRoot,
    platformRoot,
    entry,
    expectedVersion: packageJson.version,
  });
}

module.exports = {
  materialize,
  resolvePlatformRoot,
  selectPlatform,
  verifyPayload,
};

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(`zvec-grep install: ${error.message}`);
    process.exitCode = 1;
  }
}
