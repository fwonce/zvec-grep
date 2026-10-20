import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { homedir } from "node:os";
import {
  defaultReleaseVariant,
  nativePackageEntries,
  npmEnvironment,
  run,
  selectPlatform,
  sha256File,
  workspaceDir,
} from "./npm-package-core.mjs";
import { checkPublishedPackage } from "./npm-release-registry.mjs";
import { resolvePlatformRoot, verifyInstallation } from "../npm/meta/install.cjs";

export function installedPackageRoot(prefix, packageName) {
  const modules =
    process.platform === "win32"
      ? join(prefix, "node_modules")
      : join(prefix, "lib", "node_modules");
  return join(modules, ...packageName.split("/"));
}

export async function verifyInstalledRelease({ manifest, entry, version, prefix, requireCuda = false }) {
  const metaRoot = installedPackageRoot(prefix, manifest.metaPackage);
  // Registry-only meta installs can nest dependencies; artifact installs may
  // put them beside meta. Resolve exactly as the real installer does.
  const platformRoot = resolvePlatformRoot(metaRoot, entry);
  for (const [root, name] of [[metaRoot, manifest.metaPackage], [platformRoot, entry.package]]) {
    const metadata = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
    if (metadata.name !== name || metadata.version !== version ||
        (name === entry.package && metadata.zvecGrep?.variant !== defaultReleaseVariant(entry))) {
      throw new Error(`installed artifact identity or variant mismatch: ${name}`);
    }
  }
  return verifyInstallation({ metaRoot, platformRoot, entry, expectedVersion: version, requireCuda });
}

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
  return port;
}

export async function smokeInstalledPackage({
  manifest,
  entry,
  version,
  prefix,
  fixtureRoot,
}) {
  const installedMeta = installedPackageRoot(prefix, manifest.metaPackage);
  const installedPlatform = resolvePlatformRoot(installedMeta, entry);
  const binary = join(installedMeta, "bin", "zg.exe");
  if (
    (await sha256File(binary)) !==
    (await sha256File(join(installedPlatform, "bin", entry.binary)))
  ) {
    throw new Error(
      "materialized meta binary does not match the platform package",
    );
  }
  await rm(fixtureRoot, { recursive: true, force: true });
  await mkdir(fixtureRoot, { recursive: true });
  await writeFile(
    join(fixtureRoot, "fixture.txt"),
    "rust-release-smoke-marker\n",
  );
  const execute = (args, mode = "direct") =>
    run(binary, args, {
      capture: true,
      timeout: 30_000,
      cwd: fixtureRoot,
      env: {
        ...process.env,
        ZVEC_GREP_HOME: join(fixtureRoot, "home"),
        ZVEC_GREP_WORKSPACE_REGISTRY: join(fixtureRoot, "workspaces.json"),
        ZVEC_GREP_MODE: mode,
      },
    });
  if (execute(["--version"]).trim() !== version) {
    throw new Error(`installed zg version does not match ${version}`);
  }
  if (
    !execute([
      "--rg",
      "-F",
      "rust-release-smoke-marker",
      "fixture.txt",
    ]).includes("rust-release-smoke-marker")
  ) {
    throw new Error("packaged zg managed-rg search failed");
  }
  const direct = execute(["--status", "--no-color"]);
  try {
    execute([
      "--server",
      "on",
      "--listen",
      `127.0.0.1:${await availablePort()}`,
    ]);
    const status = execute(["--server", "status", "--check-ready"]);
    if (
      !status.includes("Server: ready") ||
      !status.includes("PID:") ||
      !status.includes("URL:")
    ) {
      throw new Error("packaged daemon did not become ready");
    }
    // Unlike managed --rg, workspace status in server mode crosses the IPC
    // boundary and exercises the packaged daemon's command endpoint.
    const server = execute(["--status", "--no-color"], "server");
    if (direct !== server)
      throw new Error("packaged direct/server workspace status differs");
  } finally {
    execute(["--server", "off"]);
  }
  if (!execute(["--server", "status"]).includes("Server: stopped")) {
    throw new Error("packaged daemon did not stop");
  }
  let unavailable = false;
  try {
    execute(["--status", "--no-color"], "server");
  } catch {
    unavailable = true;
  }
  if (!unavailable)
    throw new Error("server status unexpectedly succeeded without the daemon");
  console.log(
    `release smoke passed: zg ${version} (${entry.target}), managed-rg and daemon IPC`,
  );
}

export async function installArtifactRelease({
  manifest,
  release,
  tarballRoot,
  prefixRoot,
  target,
  accelerator = "cpu",
  runNpm = run,
  verifyInstalled = verifyInstalledRelease,
}) {
  if (
    release.schemaVersion !== 1 || !Array.isArray(release.packages) ||
    !/^\d+\.\d+\.\d+(?:-[\w.-]+)?(?:\+[\w.-]+)?$/.test(release.version)
  ) {
    throw new Error("invalid artifact release manifest");
  }
  const entry = selectPlatform(
    manifest,
    process.platform,
    process.arch,
    process.report?.getReport().header ?? {},
  );
  if (target && target !== entry.target) throw new Error(`artifact target ${target} does not match host ${entry.target}`);
  if (!["cpu", "auto", "cuda"].includes(accelerator)) throw new Error("invalid installation accelerator");
  const tarballs = [];
  for (const { name, target, component } of [
    ...nativePackageEntries(entry),
    { name: manifest.metaPackage, target: "meta" },
  ]) {
    if (component && accelerator === "cpu") continue;
    const matches = release.packages.filter((candidate) => candidate.name === name);
    const candidate = matches[0];
    const file = `${name.replace(/^@/, "").replaceAll("/", "-")}-${release.version}.tgz`;
    if (
      matches.length !== 1 || candidate.version !== release.version ||
      candidate.target !== target || candidate.file !== file
    ) {
      throw new Error(`artifact package identity mismatch: ${name}`);
    }
    const path = join(tarballRoot, file);
    if ((await sha256File(path)) !== candidate.sha256) {
      throw new Error(`artifact tarball checksum mismatch: ${file}`);
    }
    if (!component) tarballs.push(path);
  }
  // Use the exact downloaded bytes, a fresh prefix/cache and no registry fallback.
  // Verification must never build or repack a package.
  const destination = resolve(prefixRoot);
  const artifacts = relative(destination, resolve(tarballRoot));
  if (destination === dirname(destination) || destination === resolve(homedir()) ||
      destination === workspaceDir || (!artifacts.startsWith("..") && !isAbsolute(artifacts))) {
    throw new Error("artifact installation needs a dedicated prefix directory outside its inputs");
  }
  const existing = await readdir(destination).catch((error) => { if (error.code === "ENOENT") return []; throw error; });
  if (existing.some((name) => !["prefix", "cache", "plugin-cache", "workspace", "installation.json"].includes(name))) {
    throw new Error("refusing to replace a directory not owned by artifact installation");
  }
  await rm(prefixRoot, { recursive: true, force: true });
  const prefix = join(prefixRoot, "prefix");
  await runNpm(
    "npm",
    [
      "install", "--global", "--prefix", prefix,
      "--offline", "--no-audit", "--no-fund",
      `--allow-scripts=${tarballs.at(-1)}`, ...tarballs,
    ],
    { env: { ...npmEnvironment(join(prefixRoot, "cache")), ZG_INSTALL_ACCELERATOR: accelerator,
      ZG_PLUGIN_DIR: tarballRoot, ZG_PLUGIN_CACHE: join(prefixRoot, "plugin-cache"), ZG_INSTALL_OFFLINE: "1" } },
  );
  const installation = await verifyInstalled({ manifest, entry, version: release.version, prefix,
    requireCuda: Boolean(entry.components?.length && accelerator !== "cpu") });
  if (accelerator === "cpu" && installation.coreId && installation.components.length) {
    throw new Error("CPU artifact installation unexpectedly installed CUDA plugins");
  }
  const bundle = installedPackageRoot(prefix, manifest.metaPackage);
  await writeFile(join(prefixRoot, "installation.json"), `${JSON.stringify({
    ...installation, bundle, accelerator, commit: release.commit,
    packages: release.packages.filter((pkg) => pkg.target === entry.target || pkg.target === "meta"),
  }, null, 2)}\n`);
  return { entry, prefix, bundle };
}

export async function smokeArtifactRelease(options) {
  const result = await installArtifactRelease(options);
  await (options.probe ?? smokeInstalledPackage)({
    manifest: options.manifest,
    entry: result.entry,
    version: options.release.version,
    prefix: result.prefix,
    fixtureRoot: join(options.prefixRoot, "workspace"),
  });
  return result;
}

export async function rollbackVersion(
  manifest,
  candidate,
  registryFetch = fetch,
) {
  const response = await registryFetch(
    `https://registry.npmjs.org/${encodeURIComponent(manifest.metaPackage)}`,
    { signal: AbortSignal.timeout(30_000) },
  );
  if (!response.ok)
    throw new Error(
      `cannot determine rollback version: registry returned ${response.status}`,
    );
  const metadata = await response.json();
  const latest = metadata["dist-tags"]?.latest;
  const previous =
    latest !== candidate
      ? latest
      : Object.keys(metadata.versions ?? {})
          .filter((version) => version !== candidate)
          .sort((a, b) =>
            (metadata.time?.[b] ?? "").localeCompare(metadata.time?.[a] ?? ""),
          )[0];
  if (
    !previous ||
    previous === candidate ||
    !/^\d+\.\d+\.\d+(?:-[\w.-]+)?$/.test(previous)
  ) {
    throw new Error(
      "registry update/rollback smoke requires a distinct published baseline version",
    );
  }
  return previous;
}

export async function smokeRegistryRelease({
  manifest,
  release,
  baseline,
  prefixRoot,
  registryFetch = fetch,
  runNpm = run,
  probe = smokeInstalledPackage,
  probePrevious,
  verifyInstalled = verifyInstalledRelease,
}) {
  if (
    !/^\d+\.\d+\.\d+(?:-[\w.-]+)?$/.test(baseline ?? "") ||
    baseline === release.version
  ) {
    throw new Error(
      "registry update/rollback smoke requires a distinct baseline version",
    );
  }
  for (const entry of release.packages) {
    if (!(await checkPublishedPackage(entry, registryFetch)))
      throw new Error(
        `registry candidate is missing: ${entry.name}@${entry.version}`,
      );
  }
  const entry = selectPlatform(
    manifest,
    process.platform,
    process.arch,
    process.report?.getReport().header ?? {},
  );
  const environment = { ...npmEnvironment(join(prefixRoot, "cache")), ZG_INSTALL_ACCELERATOR: "cpu",
    ZG_PLUGIN_DIR: "", ZG_PLUGIN_CACHE: join(prefixRoot, "plugin-cache"), ZG_INSTALL_OFFLINE: "0" };
  // Resolve optional dependencies from the public registry, without installing
  // a platform tarball explicitly or reusing a local candidate's npm cache.
  const install = (prefix, version) =>
    runNpm(
      "npm",
      [
        "install",
        "--global",
        "--prefix",
        prefix,
        "--registry=https://registry.npmjs.org",
        "--no-audit",
        "--no-fund",
        `--allow-scripts=${manifest.metaPackage}`,
        `${manifest.metaPackage}@${version}`,
      ],
      { env: environment },
    );
  const verifyPrevious =
    probePrevious ??
    (async (prefix) => {
      const root = installedPackageRoot(prefix, manifest.metaPackage);
      const metadata = JSON.parse(
        await readFile(join(root, "package.json"), "utf8"),
      );
      if (metadata.version !== baseline)
        throw new Error("rollback package version differs from the baseline");
      const bin =
        typeof metadata.bin === "string" ? metadata.bin : metadata.bin?.zg;
      if (!bin) throw new Error("rollback package has no zg executable");
      const path = join(root, bin);
      const command =
        path.endsWith(".js") || path.endsWith(".cjs") || path.endsWith(".mjs")
          ? process.execPath
          : path;
      const args =
        command === process.execPath ? [path, "--version"] : ["--version"];
      if (run(command, args, { capture: true }).trim() !== baseline)
        throw new Error(
          "rollback executable version differs from the baseline",
        );
    });
  const fresh = join(prefixRoot, "fresh");
  const update = join(prefixRoot, "update");
  await rm(prefixRoot, { recursive: true, force: true });
  await install(fresh, release.version);
  const installation = await verifyInstalled({ manifest, entry, version: release.version, prefix: fresh });
  await writeFile(join(prefixRoot, "installation.json"), `${JSON.stringify(installation, null, 2)}\n`);
  await probe({
    manifest,
    entry,
    version: release.version,
    prefix: fresh,
    fixtureRoot: join(prefixRoot, "fresh-workspace"),
  });
  if (entry.components?.length) {
    // Explicit registry fetch on a CPU runner: auto fallback cannot conceal a
    // missing artifact, wrong ABI, integrity error or authentication failure.
    await runNpm("npm", ["rebuild", "--global", "--prefix", fresh,
      "--registry=https://registry.npmjs.org", `--allow-scripts=${manifest.metaPackage}`, manifest.metaPackage, "--foreground-scripts"],
    { env: { ...environment, ZG_INSTALL_ACCELERATOR: "cuda" } });
    await verifyInstalled({ manifest, entry, version: release.version, prefix: fresh, requireCuda: true });
  }
  await install(update, baseline);
  await verifyPrevious(update);
  await install(update, release.version);
  await verifyInstalled({ manifest, entry, version: release.version, prefix: update });
  await probe({
    manifest,
    entry,
    version: release.version,
    prefix: update,
    fixtureRoot: join(prefixRoot, "update-workspace"),
  });
  await install(update, baseline);
  await verifyPrevious(update);
  console.log(
    `registry install/update/rollback passed: ${baseline} -> ${release.version} -> ${baseline} (${entry.target})`,
  );
}
