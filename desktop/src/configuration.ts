type ConfigObject = Record<string, unknown>;

export type LibrarySource = ConfigObject & {
  type: "local-static" | "local-cmake" | "git";
  path?: string;
  artifact?: string;
  artifactByEnvironment?: Record<string, string>;
  includeDirs?: string[];
  repository?: string;
  ref?: string;
  checkoutDir?: string;
  cmakeSubdir?: string;
};
export type LibraryConfig = ConfigObject & { name: string; target?: string; includeDirs?: string[]; source: LibrarySource };
export type Configuration = ConfigObject & {
  schemaVersion: 1;
  project: ConfigObject & { name: string; rootDir?: string; sourceDirs?: string[]; includeDirs?: string[]; entryFile?: string };
  environment: ConfigObject & { id: string; rootDir: string; cubemxDir?: string; toolchain: ConfigObject & { file: string } };
  libraries?: LibraryConfig[];
  generation?: ConfigObject;
};
export type ProjectForm = { name: string; parentDir: string; sourceDirs: string; includeDirs: string; importedRootDir?: string };
export type EnvironmentForm = { id: string; rootDir: string; toolchain: string; cubemx: string };
export type Library = {
  name: string;
  type: LibrarySource["type"];
  target: string;
  path: string;
  artifact: string;
  repository: string;
  ref: string;
  checkoutDir: string;
  cmakeSubdir: string;
  includeDirs: string;
  original?: LibraryConfig;
};

export const emptyLibrary = (): Library => ({ name: "", type: "local-static", target: "", path: "", artifact: "", repository: "", ref: "main", checkoutDir: "third_party/library", cmakeSubdir: "", includeDirs: "" });

function parentDirectory(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const slash = normalized.lastIndexOf("/");
  if (slash < 0) return "";
  return normalized.slice(0, slash + (slash === 0 || /^[A-Za-z]:\//.test(normalized) && slash === 2 ? 1 : 0));
}

export function projectOutputDirectory(project: ProjectForm): string {
  if (project.importedRootDir !== undefined) return project.importedRootDir;
  const parent = project.parentDir.trim().replace(/[\\/]+$/, "");
  const name = project.name.trim();
  if (!parent && /^[\\/]+$/.test(project.parentDir.trim())) return name ? `/${name}` : "/";
  return parent && name ? `${parent}/${name}` : parent;
}

function libraryIncludes(library: LibraryConfig): string[] {
  return [...new Set([...(library.includeDirs ?? []), ...(library.source.type === "local-static" ? library.source.includeDirs ?? [] : [])])];
}

// The bridge checks schema types and resolves config-relative paths before this is called.
export function configurationToForm(config: Configuration, output: string) {
  return {
    project: {
      name: config.project.name,
      parentDir: parentDirectory(output),
      importedRootDir: output,
      sourceDirs: (config.project.sourceDirs ?? ["src"]).join(","),
      includeDirs: (config.project.includeDirs ?? ["inc"]).join(","),
    } satisfies ProjectForm,
    environment: {
      id: config.environment.id,
      rootDir: config.environment.rootDir,
      toolchain: config.environment.toolchain.file,
      cubemx: config.environment.cubemxDir ?? "",
    },
    libraries: (config.libraries ?? []).map((library): Library => ({
      ...emptyLibrary(),
      original: library,
      name: library.name,
      type: library.source.type,
      target: library.target ?? "",
      path: library.source.path ?? "",
      artifact: library.source.artifact ?? "",
      repository: library.source.repository ?? "",
      ref: library.source.ref ?? "main",
      checkoutDir: library.source.checkoutDir ?? `libs/${library.name}`,
      cmakeSubdir: library.source.cmakeSubdir ?? "",
      includeDirs: libraryIncludes(library).join("\n"),
    })),
  };
}

function directoryList(value: string, original: string[] | undefined): string[] {
  // Preserve imported paths containing commas until the field is edited.
  return original && value === original.join(",") ? original : value.split(",").map((item) => item.trim()).filter(Boolean);
}

export function formToConfiguration(project: ProjectForm, environment: EnvironmentForm, libraries: Library[], original?: Configuration): Configuration {
  const sourceDirs = directoryList(project.sourceDirs, original?.project.sourceDirs);
  const firstSourceDir = sourceDirs[0];
  const entryFile = firstSourceDir && !firstSourceDir.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(firstSourceDir) && !firstSourceDir.includes("..")
    ? `${firstSourceDir.replace(/\\/g, "/").replace(/\/$/, "")}/app_main.c`
    : "src/app_main.c";
  return {
    ...original,
    schemaVersion: 1,
    project: {
      scanSources: true, generateExampleMain: true, compileStandard: "gnu11", entryFile,
      ...original?.project,
      name: project.name, target: project.name, rootDir: projectOutputDirectory(project), sourceDirs,
      includeDirs: directoryList(project.includeDirs, original?.project.includeDirs),
    },
    environment: {
      ...original?.environment,
      id: environment.id, rootDir: environment.rootDir, cubemxDir: environment.cubemx || undefined,
      toolchain: { ...original?.environment.toolchain, file: environment.toolchain },
    },
    libraries: libraries.map((library): LibraryConfig => {
      const originalLibrary = library.original;
      const source: LibrarySource = {
        ...(originalLibrary?.source.type === library.type ? originalLibrary.source : {}),
        ...(library.type === "git"
          ? { type: library.type, repository: library.repository, ref: library.ref, checkoutDir: library.checkoutDir, cmakeSubdir: library.cmakeSubdir.trim() || "." }
          : library.type === "local-cmake"
            ? { type: library.type, path: library.path }
            : { type: library.type, ...(library.artifact || !originalLibrary?.source.artifactByEnvironment ? { artifact: library.artifact } : {}) }),
      };
      if (library.type === "local-static" && !library.artifact && source.artifactByEnvironment) delete source.artifact;
      const includeDirs = library.includeDirs.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      const includesUnchanged = originalLibrary?.source.type === library.type && library.includeDirs === libraryIncludes(originalLibrary).join("\n");
      if (!includesUnchanged) delete source.includeDirs;
      return {
        ...originalLibrary, name: library.name, target: library.target || library.name,
        includeDirs: includesUnchanged ? originalLibrary.includeDirs : includeDirs, source,
      };
    }),
    generation: original ? original.generation : { buildDir: "build", generator: "Ninja", buildScript: true, outputFormats: ["elf", "hex", "bin"], sizeReport: true },
  };
}
