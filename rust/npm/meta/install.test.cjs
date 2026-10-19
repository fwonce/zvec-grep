"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const {
  chmodSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} = require("node:fs");
const { join } = require("node:path");
const { tmpdir } = require("node:os");
const test = require("node:test");

const { materialize, selectPlatform, verifyPayload } = require("./install.cjs");

const entry = {
  target: "darwin-arm64",
  package: "@zvec/zvec-grep-darwin-arm64",
  os: "darwin",
  cpu: "arm64",
  binary: "zg",
};

function sha256(contents) {
  return createHash("sha256").update(contents).digest("hex");
}

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "zvec-grep-npm-install-"));
  const metaRoot = join(root, "meta");
  const platformRoot = join(root, "platform");
  mkdirSync(join(metaRoot, "bin"), { recursive: true });
  mkdirSync(join(platformRoot, "bin"), { recursive: true });
  mkdirSync(join(platformRoot, "lib"), { recursive: true });
  const binary = Buffer.from("#!/bin/sh\necho 0.0.1\n");
  const library = Buffer.from("native-library-fixture\n");
  writeFileSync(join(metaRoot, "bin", "zg.exe"), "placeholder\n");
  writeFileSync(join(platformRoot, "bin", "zg"), binary);
  chmodSync(join(platformRoot, "bin", "zg"), 0o755);
  writeFileSync(join(platformRoot, "lib", "libbackend.dylib"), library);
  writeFileSync(join(platformRoot, "lib", "libfixture.dylib"), library);
  writeFileSync(
    join(platformRoot, "package.json"),
    JSON.stringify({ name: entry.package, version: "0.0.1" }),
  );
  writeFileSync(
    join(platformRoot, "checksums.json"),
    JSON.stringify({
      schemaVersion: 1,
      target: entry.target,
      files: {
        "bin/zg": { bytes: binary.length, sha256: sha256(binary) },
        "lib/libbackend.dylib": { bytes: library.length, sha256: sha256(library) },
        "lib/libfixture.dylib": { bytes: library.length, sha256: sha256(library) },
      },
    }),
  );
  return { root, metaRoot, platformRoot };
}

test("materializes a verified native package and replaces the placeholder last", () => {
  const paths = fixture();
  try {
    materialize({ ...paths, entry, expectedVersion: "0.0.1" });
    assert.match(readFileSync(join(paths.metaRoot, "bin", "zg.exe"), "utf8"), /0\.0\.1/);
    assert.equal(
      readFileSync(join(paths.metaRoot, "lib", "libbackend.dylib"), "utf8"),
      "native-library-fixture\n",
    );
    assert.equal(
      readFileSync(join(paths.metaRoot, "lib", "libfixture.dylib"), "utf8"),
      "native-library-fixture\n",
    );
    assert.notEqual(statSync(join(paths.metaRoot, "bin", "zg.exe")).mode & 0o111, 0);
  } finally {
    rmSync(paths.root, { recursive: true, force: true });
  }
});

test("rejects version and checksum mismatches without replacing the placeholder", () => {
  const paths = fixture();
  try {
    assert.throws(
      () => verifyPayload(paths.platformRoot, entry, "0.0.2"),
      /native package mismatch/,
    );
    writeFileSync(join(paths.platformRoot, "bin", "zg"), "corrupt\n");
    assert.throws(
      () => materialize({ ...paths, entry, expectedVersion: "0.0.1" }),
      /checksum mismatch/,
    );
    assert.equal(readFileSync(join(paths.metaRoot, "bin", "zg.exe"), "utf8"), "placeholder\n");
  } finally {
    rmSync(paths.root, { recursive: true, force: true });
  }
});

test("rejects native files that are not covered by the checksum manifest", () => {
  const paths = fixture();
  try {
    writeFileSync(join(paths.platformRoot, "bin", "untracked.dylib"), "untracked\n");
    assert.throws(
      () => verifyPayload(paths.platformRoot, entry, "0.0.1"),
      /do not match the checksum manifest/,
    );
  } finally {
    rmSync(paths.root, { recursive: true, force: true });
  }
});

test("selects one published platform and rejects unsupported libc", () => {
  assert.equal(selectPlatform([entry], "darwin", "arm64"), entry);
  assert.throws(
    () => selectPlatform([entry], "linux", "x64", { glibcVersionRuntime: "2.39" }),
    /does not publish/,
  );
});

function splitFixture(target = "linux-x64-gnu") {
  const paths = fixture();
  const platform = require("../platforms.json").platforms.find((candidate) => candidate.target === target);
  const manifest = JSON.parse(readFileSync(join(paths.platformRoot, "checksums.json"), "utf8"));
  manifest.schemaVersion = 2;
  manifest.target = target;
  manifest.components = platform.components.map((component) => component.package);
  if (platform.binary !== "zg") {
    renameSync(join(paths.platformRoot, "bin/zg"), join(paths.platformRoot, "bin", platform.binary));
    manifest.files[`bin/${platform.binary}`] = manifest.files["bin/zg"];
    delete manifest.files["bin/zg"];
  }
  const originals = new Map();
  for (const file of new Set(platform.components.map((component) => component.file))) {
    const contents = Buffer.alloc(2 * 1024 * 1024 + 37);
    for (let i = 0; i < contents.length; i++) contents[i] = (i + file.length) % 251;
    originals.set(file, contents);
    manifest.files[file] = { bytes: contents.length, sha256: sha256(contents) };
    const siblings = platform.components.filter((component) => component.file === file);
    for (const [index, component] of siblings.entries()) {
      const directory = join(paths.root, "node_modules", component.package);
      mkdirSync(join(directory, component.payload, ".."), { recursive: true });
      const bytes = contents.subarray(Math.floor(contents.length * index / siblings.length),
        Math.floor(contents.length * (index + 1) / siblings.length));
      writeFileSync(join(directory, component.payload), bytes);
      writeFileSync(join(directory, "package.json"), JSON.stringify({ name: component.package, version: "0.0.1" }));
      writeFileSync(join(directory, "checksums.json"), JSON.stringify({
        schemaVersion: 1, target, component: component.package,
        files: { [component.payload]: { bytes: bytes.length, sha256: sha256(bytes) } },
      }));
    }
  }
  writeFileSync(join(paths.platformRoot, "checksums.json"), JSON.stringify(manifest));
  writeFileSync(join(paths.platformRoot, "package.json"), JSON.stringify({
    name: platform.package, version: "0.0.1",
    dependencies: Object.fromEntries(platform.components.map((component) => [component.package, "0.0.1"]).reverse()),
  }));
  return { ...paths, entry: platform, originals };
}

for (const target of ["linux-x64-gnu", "win32-x64-msvc"]) {
  test(`assembles ${target} CUDA components into the unchanged bin/lib layout`, (t) => {
    const paths = splitFixture(target);
    t.after(() => rmSync(paths.root, { recursive: true, force: true }));
    materialize({ ...paths, expectedVersion: "0.0.1" });
    for (const [file, contents] of paths.originals) {
      assert.deepEqual(readFileSync(join(paths.metaRoot, file)), contents);
    }
    assert.match(readFileSync(join(paths.metaRoot, "bin/zg.exe"), "utf8"), /0\.0\.1/);
    // Reinstallation also verifies and replaces an existing complete runtime.
    materialize({ ...paths, expectedVersion: "0.0.1" });
  });
}

for (const failure of ["missing", "version", "corrupt", "swapped", "untracked", "traversal"]) {
  test(`rejects ${failure} components without replacing the previous installation`, (t) => {
    const paths = splitFixture();
    t.after(() => rmSync(paths.root, { recursive: true, force: true }));
    mkdirSync(join(paths.metaRoot, "lib"), { recursive: true });
    writeFileSync(join(paths.metaRoot, "lib/previous.so"), "previous installation");
    const parts = paths.entry.components.filter((component) => component.payload.startsWith("parts/"));
    const component = parts[0];
    const directory = join(paths.root, "node_modules", component.package);
    const checksums = JSON.parse(readFileSync(join(directory, "checksums.json"), "utf8"));
    if (failure === "missing") rmSync(directory, { recursive: true, force: true });
    if (failure === "version") writeFileSync(join(directory, "package.json"), JSON.stringify({ name: component.package, version: "0.0.2" }));
    if (failure === "corrupt") writeFileSync(join(directory, component.payload), "corrupt");
    if (failure === "untracked") writeFileSync(join(directory, "parts/untracked"), "unexpected");
    if (failure === "traversal") {
      checksums.files = { "parts/../../outside": Object.values(checksums.files)[0] };
      writeFileSync(join(directory, "checksums.json"), JSON.stringify(checksums));
    }
    if (failure === "swapped") {
      // Make the fragment's own hash valid: only the final complete-file hash
      // can detect a valid fragment being put in the wrong position.
      const wrong = readFileSync(join(paths.root, "node_modules", parts[1].package, parts[1].payload));
      writeFileSync(join(directory, component.payload), wrong);
      checksums.files[component.payload] = { bytes: wrong.length, sha256: sha256(wrong) };
      writeFileSync(join(directory, "checksums.json"), JSON.stringify(checksums));
    }
    assert.throws(() => materialize({ ...paths, expectedVersion: "0.0.1" }),
      /missing|mismatch|checksum|manifest|ENOENT/);
    assert.equal(readFileSync(join(paths.metaRoot, "bin/zg.exe"), "utf8"), "placeholder\n");
    assert.equal(readFileSync(join(paths.metaRoot, "lib/previous.so"), "utf8"), "previous installation");
  });
}
