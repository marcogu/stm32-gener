import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Compile the pure configuration mapper with the project's existing compiler.
// A data URL avoids temporary build files and works without Node's TS loader.
const source = readFileSync(new URL("../src/configuration.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { configurationToForm, emptyLibrary, formToConfiguration, projectOutputDirectory } =
  await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function fixture() {
  return {
    schemaVersion: 1,
    workspace: { rootDir: "/workspace", configFile: "saved.json" },
    project: {
      name: "demo", target: "demo", rootDir: "/workspace/custom application",
      version: "2.3.4", sourceDirs: ["source,with,commas", "other source"], includeDirs: ["include,comma"],
      sourceFiles: ["other source/application.c"], scanSources: false, entryFile: "custom/main.c",
      generateExampleMain: false, compileStandard: "c17", compileOptions: ["-Wall"],
      compileDefinitions: ["DEMO=1"], linkLibraries: ["m"],
    },
    environment: {
      id: "board", displayName: "My board", rootDir: "/workspace/environment",
      cubemxDir: "/workspace/environment/cmake/stm32cubemx",
      toolchain: {
        file: "/workspace/environment/toolchain.cmake", binDir: "/tools/arm/bin",
        compilerPrefix: "custom-arm-", cmakeGenerator: "Unix Makefiles", cmakeMinimumVersion: "3.24",
      },
      device: { part: "STM32F407", defines: ["BOARD=1"] },
      profiles: { Debug: { compileOptions: ["-Og"], linkOptions: ["-Wl,--gc-sections"] } },
    },
    libraries: [
      {
        name: "binary", displayName: "Prebuilt binary", target: "binary_target",
        includeDirs: ["/workspace/shared headers"], compileDefinitions: ["BINARY=1"],
        compileOptions: ["-Wextra"], linkLibraries: ["m"],
        source: {
          type: "local-static", artifactByEnvironment: { board: "/libs/board.a", alternate: "/libs/alternate.a" },
          includeDirs: ["/libs/headers", "/workspace/shared headers"],
        },
      },
      { name: "local", source: { type: "local-cmake", path: "/libs/local cmake" } },
      {
        name: "remote", target: "remote_target", includeDirs: ["include"],
        source: {
          type: "git", repository: "https://example.invalid/library.git", ref: "v2",
          checkoutDir: "third_party/remote", cmakeSubdir: "components/remote", fetchContentName: "RemoteSource",
          updateDisconnection: true,
        },
      },
    ],
    generation: {
      buildDir: "out/build", generator: "Unix Makefiles", buildScript: false, outputFormats: ["elf"],
      sizeReport: false, postBuildTools: { size: "/tools/size", objcopy: "/tools/objcopy" },
      conflictPolicy: "backup", runConfigure: false, runBuild: false,
    },
  };
}

function importForm(config) {
  return configurationToForm(config, config.project.rootDir ?? "");
}

function exportConfig(form, original) {
  // Test the JSON that is saved, including omission of undefined properties.
  return JSON.parse(JSON.stringify(formToConfiguration(form.project, form.environment, form.libraries, original)));
}

test("import, edit, and export retain advanced project/environment/generation fields", () => {
  const original = fixture();
  const before = structuredClone(original);
  const form = importForm(original);
  form.environment.toolchain = "/replacement/toolchain.cmake";
  form.libraries[2].ref = "v3";
  const saved = exportConfig(form, original);

  assert.deepEqual(saved.project, original.project);
  assert.deepEqual(saved.workspace, original.workspace);
  assert.deepEqual(saved.environment, {
    ...original.environment,
    toolchain: { ...original.environment.toolchain, file: "/replacement/toolchain.cmake" },
  });
  assert.deepEqual(saved.generation, original.generation);
  assert.deepEqual(saved.libraries[2], {
    ...original.libraries[2], source: { ...original.libraries[2].source, ref: "v3" },
  });
  assert.deepEqual(original, before, "editing the form must not mutate the imported configuration");
});

test("all library kinds retain their source settings, including static artifact maps and source includes", () => {
  const original = fixture();
  const form = importForm(original);
  assert.equal(form.libraries[0].includeDirs, "/workspace/shared headers\n/libs/headers");
  const saved = exportConfig(form, original);
  for (const [index, library] of original.libraries.entries()) {
    assert.deepEqual(saved.libraries[index].source, library.source);
    assert.deepEqual(saved.libraries[index].includeDirs, library.includeDirs);
  }
  assert.equal(saved.libraries[0].source.artifact, undefined, "an artifact map needs no empty artifact override");
});

test("editing merged static includes removes stale source includes without losing artifact maps", () => {
  const original = fixture();
  const form = importForm(original);
  form.libraries[0].includeDirs = "/new headers\r\n/another directory\n";
  const saved = exportConfig(form, original).libraries[0];
  assert.deepEqual(saved.includeDirs, ["/new headers", "/another directory"]);
  assert.equal(saved.source.includeDirs, undefined);
  assert.deepEqual(saved.source.artifactByEnvironment, original.libraries[0].source.artifactByEnvironment);
  assert.deepEqual(saved.compileDefinitions, ["BINARY=1"]);
});

test("clearing a static artifact allows the retained environment map to select it", () => {
  const original = fixture();
  original.libraries[0].source.artifact = "/libs/fallback.a";
  const form = importForm(original);
  form.libraries[0].artifact = "";
  let source = exportConfig(form, original).libraries[0].source;
  assert.equal(source.artifact, undefined);
  assert.deepEqual(source.artifactByEnvironment, original.libraries[0].source.artifactByEnvironment);

  form.libraries[0].artifact = "/libs/replacement.a";
  source = exportConfig(form, original).libraries[0].source;
  assert.equal(source.artifact, "/libs/replacement.a");
  assert.deepEqual(source.artifactByEnvironment, original.libraries[0].source.artifactByEnvironment);
});

test("changing library source kind drops incompatible source fields but retains compile settings", () => {
  const original = fixture();
  const form = importForm(original);
  Object.assign(form.libraries[0], {
    type: "git", repository: "https://example.invalid/new.git", ref: "main",
    checkoutDir: "third_party/new", cmakeSubdir: "driver",
  });
  const saved = exportConfig(form, original).libraries[0];
  assert.deepEqual(saved.source, {
    type: "git", repository: "https://example.invalid/new.git", ref: "main",
    checkoutDir: "third_party/new", cmakeSubdir: "driver",
  });
  assert.deepEqual(saved.compileOptions, ["-Wextra"]);
  assert.deepEqual(saved.includeDirs, ["/workspace/shared headers", "/libs/headers"]);
});

test("Git SOURCE_SUBDIR supports omitted, blank, root, and nested directories", () => {
  for (const value of [undefined, "", "   ", ".", " components/driver cmake "]) {
    const original = fixture();
    if (value === undefined) delete original.libraries[2].source.cmakeSubdir;
    else original.libraries[2].source.cmakeSubdir = value;
    const saved = exportConfig(importForm(original), original);
    assert.equal(saved.libraries[2].source.cmakeSubdir, value?.trim() || ".");
    assert.equal(saved.libraries[2].source.checkoutDir, "third_party/remote");
  }
});

test("custom imported output roots survive unrelated edits and can be reset for a new name or parent", () => {
  for (const root of ["/workspace/custom application", "C:\\workspace\\custom application"]) {
    const original = fixture();
    original.project.rootDir = root;
    const form = importForm(original);
    assert.equal(projectOutputDirectory(form.project), root);
    form.environment.id = "another_board";
    assert.equal(exportConfig(form, original).project.rootDir, root);

    const renamed = { ...form.project, name: "new_name", importedRootDir: undefined };
    assert.equal(projectOutputDirectory(renamed), root.startsWith("C:") ? "C:/workspace/new_name" : "/workspace/new_name");
    const relocated = { ...form.project, parentDir: "/new workspace/", importedRootDir: undefined };
    assert.equal(projectOutputDirectory(relocated), "/new workspace/demo");
  }
});

test("comma-containing imported source and include paths survive unrelated edits", () => {
  const original = fixture();
  const form = importForm(original);
  form.libraries[0].name = "new_binary_name";
  let saved = exportConfig(form, original);
  assert.deepEqual(saved.project.sourceDirs, ["source,with,commas", "other source"]);
  assert.deepEqual(saved.project.includeDirs, ["include,comma"]);

  form.project.sourceDirs = " src, application/src, ";
  form.project.includeDirs = " inc, application/include ";
  saved = exportConfig(form, original);
  assert.deepEqual(saved.project.sourceDirs, ["src", "application/src"]);
  assert.deepEqual(saved.project.includeDirs, ["inc", "application/include"]);
  assert.equal(saved.project.entryFile, "custom/main.c");
});

test("an omitted generation object preserves the imported toolchain generator default", () => {
  const original = fixture();
  delete original.generation;
  const saved = exportConfig(importForm(original), original);
  const effectiveGenerator = saved.generation?.generator ?? saved.environment.toolchain.cmakeGenerator ?? "Ninja";
  assert.equal(effectiveGenerator, "Unix Makefiles");
});

test("a new draft with no parent does not invent an output path and can be exported", () => {
  const project = { name: "draft", parentDir: "", sourceDirs: "src", includeDirs: "inc" };
  const environment = { id: "", rootDir: "", toolchain: "", cubemx: "" };
  const library = { ...emptyLibrary(), type: "git" };
  const saved = exportConfig({ project, environment, libraries: [library] });
  assert.equal(projectOutputDirectory(project), "");
  assert.equal(saved.project.rootDir, "");
  assert.equal(saved.project.name, "draft");
  assert.equal(saved.environment.rootDir, "");
  assert.equal(saved.libraries[0].source.cmakeSubdir, ".");
  assert.equal(saved.generation.generator, "Ninja");
  const restored = exportConfig(configurationToForm(saved, ""), saved);
  assert.equal(restored.project.rootDir, "");
  assert.equal(restored.project.name, "draft");
});
