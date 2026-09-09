import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import "./App.css";

type Library = { name: string; type: "local-static" | "local-cmake" | "git"; target: string; path: string; artifact: string; repository: string; ref: string; checkoutDir: string; includeDirs: string };
const emptyLibrary = (): Library => ({ name: "", type: "local-static", target: "", path: "", artifact: "", repository: "", ref: "main", checkoutDir: "third_party/library", includeDirs: "" });

function App() {
  const [step, setStep] = useState(0);
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState<{ title: string; message: string } | null>(null);
  const errorBanner = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [diff, setDiff] = useState("");
  const [project, setProject] = useState({ name: "demo_app", parentDir: "", sourceDirs: "src", includeDirs: "inc" });
  const [environment, setEnvironment] = useState({ id: "", rootDir: "", toolchain: "", cubemx: "" });
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [generation] = useState({ buildDir: "build", generator: "Ninja", buildScript: true, formats: ["elf", "hex", "bin"], sizeReport: true });

  const projectOutputDir = useMemo(() => {
    const parent = project.parentDir.trim().replace(/[\\/]+$/, "");
    const name = project.name.trim();
    return parent && name ? `${parent}/${name}` : parent;
  }, [project.name, project.parentDir]);

  useEffect(() => {
    if (error) {
      errorBanner.current?.focus({ preventScroll: true });
      errorBanner.current?.scrollIntoView({ block: "nearest" });
    }
  }, [error]);

  const config = useMemo(() => {
    const sourceDirs = project.sourceDirs.split(",").map((v) => v.trim()).filter(Boolean);
    const firstSourceDir = sourceDirs[0];
    const entryFile = firstSourceDir && !firstSourceDir.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(firstSourceDir) && !firstSourceDir.includes("..")
      ? `${firstSourceDir.replace(/\\/g, "/").replace(/\/$/, "")}/app_main.c`
      : "src/app_main.c";
    return ({
    schemaVersion: 1,
    project: { name: project.name, target: project.name, rootDir: projectOutputDir, sourceDirs, includeDirs: project.includeDirs.split(",").map((v) => v.trim()).filter(Boolean), scanSources: true, entryFile, generateExampleMain: true, compileStandard: "gnu11" },
    environment: { id: environment.id, rootDir: environment.rootDir, cubemxDir: environment.cubemx || undefined, toolchain: { file: environment.toolchain } },
    libraries: libraries.map((lib) => ({ name: lib.name, target: lib.target || lib.name, includeDirs: lib.includeDirs.split(",").map((v) => v.trim()).filter(Boolean), source: lib.type === "git" ? { type: "git", repository: lib.repository, ref: lib.ref, checkoutDir: lib.checkoutDir } : lib.type === "local-cmake" ? { type: "local-cmake", path: lib.path } : { type: "local-static", artifact: lib.artifact, includeDirs: lib.includeDirs.split(",").map((v) => v.trim()).filter(Boolean) } })),
    generation: { buildDir: generation.buildDir, generator: generation.generator, buildScript: generation.buildScript, outputFormats: generation.formats, sizeReport: generation.sizeReport },
    });
  }, [environment, generation, libraries, project, projectOutputDir]);

  function errorMessage(value: unknown): string {
    const detail = value && typeof value === "object" ? value as { message?: unknown; error?: unknown } : null;
    const message = typeof value === "string" ? value : typeof detail?.message === "string" ? detail.message : typeof detail?.error === "string" ? detail.error : "An unexpected error occurred. Please check your settings and try again.";
    return message.replace(/^Error:\s*/i, "").trim() || "Unknown error";
  }

  function fail(message: string, targetStep = step, title = "Check your settings") {
    setError({ title, message });
    setStatus(title);
    setStep(targetStep);
  }

  function validateStep(index: number): string | null {
    if (index === 0) {
      if (!project.name.trim()) return "Project name is required.";
      if (!project.parentDir.trim()) return "Parent directory is required.";
    }
    if (index === 1) {
      if (!environment.rootDir.trim()) return "Environment root is required.";
      if (!environment.id.trim()) return "Environment ID is required. Enter it or scan the environment.";
      if (!environment.toolchain.trim()) return "Toolchain file is required.";
    }
    if (index === 2) {
      for (const [index, library] of libraries.entries()) {
        const label = `Library ${index + 1}`;
        if (!library.name.trim()) return `${label}: name is required.`;
        if (library.type === "local-static" && !library.artifact.trim()) return `${label}: static archive is required.`;
        if (library.type === "local-cmake" && !library.path.trim()) return `${label}: CMake root is required.`;
        if (library.type === "git" && !library.repository.trim()) return `${label}: repository is required.`;
        if (library.type === "git" && !library.ref.trim()) return `${label}: ref is required.`;
        if (library.type === "git" && !library.checkoutDir.trim()) return `${label}: checkout directory is required.`;
      }
    }
    return null;
  }

  function validateAll(): { message: string; step: number } | null {
    for (const index of [0, 1, 2]) {
      const message = validateStep(index);
      if (message) return { message, step: index };
    }
    return null;
  }

  async function scan(kind: "environment" | "project", pathOverride?: string) {
    const path = pathOverride || (kind === "environment" ? environment.rootDir : projectOutputDir);
    if (!path.trim()) return fail("Choose a directory first", kind === "environment" ? 1 : 0);
    setError(null);
    setStatus("Scanning directory…");
    try {
      const result = await invoke<Record<string, unknown>>("bridge", { request: { action: "scan", kind, path } });
      if (kind === "environment") {
        const env = result as { id?: string; toolchainFiles?: string[]; cubemxDir?: string | null };
        setEnvironment((value) => ({ ...value, id: env.id || "", rootDir: path, toolchain: env.toolchainFiles?.[0] || "", cubemx: env.cubemxDir || "" }));
        setStatus(env.toolchainFiles?.length && env.cubemxDir ? "CubeMX CMake environment detected" : "Scan complete");
      } else {
        const found = result as { name?: string; sourceFiles?: string[]; includeDirs?: string[] };
        setProject((value) => ({ ...value, name: found.name || value.name, sourceDirs: [...new Set((found.sourceFiles || []).map((path) => path.split("/")[0]))].join(","), includeDirs: (found.includeDirs || []).join(",") }));
      }
      if (kind !== "environment") setStatus("Scan complete");
    } catch (error) { fail(errorMessage(error), kind === "environment" ? 1 : 0, "Scan failed"); }
  }

  async function previewGeneration(action: "preview" | "generate") {
    if (busy) return;
    const invalid = validateAll();
    if (invalid) return fail(invalid.message, invalid.step);
    setError(null);
    setDiff("");
    setBusy(true);
    setStatus(action === "generate" ? "Generating project…" : "Preparing preview…");
    try {
      const result = await invoke<{ diff: string }>("bridge", { request: { action, config, configDir: ".", output: projectOutputDir } });
      setDiff(result.diff);
      setStatus(action === "generate" ? "Project generated" : "Preview updated");
      setStep(3);
    } catch (error) { fail(errorMessage(error), 3, action === "generate" ? "Generation failed" : "Preview failed"); }
    finally { setBusy(false); }
  }

  const updateProject = (key: keyof typeof project) => (event: React.ChangeEvent<HTMLInputElement>) => setProject({ ...project, [key]: event.target.value });
  const updateEnvironment = (key: keyof typeof environment) => (event: React.ChangeEvent<HTMLInputElement>) => setEnvironment({ ...environment, [key]: event.target.value });
  async function chooseProjectDirectory() {
    try {
      const selected = await open({ directory: true, multiple: false, title: "Select parent directory", defaultPath: project.parentDir || undefined });
      if (selected) setProject((value) => ({ ...value, parentDir: selected }));
    } catch (error) { fail(errorMessage(error), 0, "Directory selection failed"); }
  }
  async function chooseEnvironmentRoot() {
    try {
      const selected = await open({ directory: true, multiple: false, title: "Select environment root", defaultPath: environment.rootDir || undefined });
      if (selected) {
        setEnvironment({ id: "", rootDir: selected, toolchain: "", cubemx: "" });
        await scan("environment", selected);
      }
    } catch (error) { fail(errorMessage(error), 1, "Directory selection failed"); }
  }

  return <main className="app-shell">
    <header className="topbar"><div className="brand"><img className="brand-mark" src="/icon.svg" alt="" /><span className="brand-name">CMake Forge</span></div><span className={error ? "status status-error" : "status"}><i className="status-dot" />{status}</span></header>
    <div className="workspace">
      <aside className="sidebar"><div className="eyebrow">PROJECT BUILDER</div><nav>{["Project", "Environment", "Libraries", "Review"].map((label, index) => <button key={label} className={step === index ? "nav-item active" : "nav-item"} onClick={() => setStep(index)}><span className="nav-number">0{index + 1}</span>{label}</button>)}</nav><div className="sidebar-foot"><span className="tiny-label">SCHEMA</span><strong>v1</strong><span className="tiny-label">TARGET</span><strong>{environment.id || "Not selected"}</strong></div></aside>
      <section className="content"><div className="page-heading"><div><span className="kicker">STEP 0{step + 1} / 04</span><h1>{["Project identity", "Build environment", "Library sources", "Review & generate"][step]}</h1><p>{["Define the application directory and source layout.", "Connect an existing CubeMX environment and ARM toolchain.", "Choose local artifacts or Git dependencies per library.", "Inspect the generated files before writing them to disk."][step]}</p></div><div className="progress"><span style={{ width: `${((step + 1) / 4) * 100}%` }} /></div></div>
        {error && <div className="error-banner" role="alert" ref={errorBanner} tabIndex={-1}><strong>{error.title}</strong><span>{error.message}</span><button type="button" onClick={() => { setError(null); setStatus("Ready"); }} aria-label="Dismiss error">×</button></div>}
        {step === 0 && <Panel title="Application" note="A folder named after the project is created inside the selected parent directory. Missing source and include folders are created inside it."><div className="form-grid"><Field label="Project name" value={project.name} onChange={updateProject("name")} required /><DirectoryField label="Parent directory" value={project.parentDir} onChange={updateProject("parentDir")} onBrowse={chooseProjectDirectory} placeholder="/path/to/workspace" required /><Field label="Source directories" value={project.sourceDirs} onChange={updateProject("sourceDirs")} /><Field label="Include directories" value={project.includeDirs} onChange={updateProject("includeDirs")} /><div className="field action-field"><label>Discovery</label><button className="secondary" onClick={() => scan("project")}>Scan project</button></div></div></Panel>}
        {step === 1 && <Panel title="Environment" note="Select the environment root; toolchain and CubeMX are detected automatically."><div className="form-grid"><DirectoryField label="Environment root" value={environment.rootDir} onChange={updateEnvironment("rootDir")} onBrowse={chooseEnvironmentRoot} placeholder="/path/to/envs/stm32f407vet6" required /><Field label="Environment ID" value={environment.id} onChange={updateEnvironment("id")} placeholder="stm32f407vet6" required /><Field label="Toolchain file" value={environment.toolchain} onChange={updateEnvironment("toolchain")} placeholder=".../cmake/arm-none-eabi.cmake" required /><Field label="CubeMX module (optional)" value={environment.cubemx} onChange={updateEnvironment("cubemx")} placeholder="Defaults to environment/cmake/stm32cubemx" /><div className="field action-field"><label>Detection</label><button className="secondary" onClick={() => scan("environment")}>Scan environment</button></div></div></Panel>}
        {step === 2 && <Panel title="Dependencies" note="Each entry is rendered as a CMake target. Git sources are fetched during configure."><div className="library-list">{libraries.map((lib, index) => <LibraryRow key={index} library={lib} onChange={(next) => setLibraries(libraries.map((item, itemIndex) => itemIndex === index ? next : item))} onRemove={() => setLibraries(libraries.filter((_, itemIndex) => itemIndex !== index))} />)}</div><button className="add-button" onClick={() => setLibraries([...libraries, emptyLibrary()])}>+ Add library</button></Panel>}
        {step === 3 && <Panel title="Generated files" note="Review the generated diff before writing it to the output directory."><div className="review-toolbar"><button className="secondary" disabled={busy} onClick={() => previewGeneration("preview")}>Refresh preview</button><button className="primary" disabled={busy} onClick={() => previewGeneration("generate")}>{busy ? "Working…" : "Generate project"}</button></div><pre className="diff">{diff || "No preview yet. Configure the project, then refresh preview."}</pre></Panel>}
        <footer className="actions"><button className="ghost" disabled={step === 0 || busy} onClick={() => setStep(step - 1)}>Back</button>{step < 3 ? <button className="primary" disabled={busy} onClick={() => { const message = validateStep(step); if (message) return fail(message, step); setError(null); setStatus("Ready"); setStep(step + 1); }}>Continue <span>→</span></button> : <button className="secondary" disabled={busy} onClick={() => previewGeneration("preview")}>Preview <span>↗</span></button>}</footer>
      </section>
    </div>
  </main>;
}

function Panel({ title, note, children }: { title: string; note: string; children: React.ReactNode }) { return <section className="panel"><div className="panel-heading"><h2>{title}</h2><p>{note}</p></div>{children}</section>; }
function Field({ label, value, onChange, placeholder, required = false }: { label: string; value: string; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; placeholder?: string; required?: boolean }) { return <div className="field"><label>{label}{required && <span className="required-mark"> *</span>}</label><input value={value} onChange={onChange} placeholder={placeholder} required={required} aria-required={required} /></div>; }
function DirectoryField({ label, value, onChange, onBrowse, placeholder, required = false }: { label: string; value: string; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; onBrowse: () => void; placeholder?: string; required?: boolean }) { return <div className="field"><label>{label}{required && <span className="required-mark"> *</span>}</label><div className="path-control"><input value={value} onChange={onChange} onClick={onBrowse} placeholder={placeholder} aria-label={`${label} path`} required={required} aria-required={required} /><button type="button" className="browse-button" onClick={onBrowse} title={`Choose ${label.toLowerCase()}`} aria-label={`Choose ${label.toLowerCase()}`}><span className="folder-icon" aria-hidden="true" /></button></div></div>; }
function LibraryRow({ library, onChange, onRemove }: { library: Library; onChange: (next: Library) => void; onRemove: () => void }) { const set = (key: keyof Library) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange({ ...library, [key]: event.target.value }); return <div className="library-row"><div className="library-row-top"><input className="library-name" value={library.name} onChange={set("name")} placeholder="library name *" required aria-required="true" /><select value={library.type} onChange={set("type")}><option value="local-static">Local static</option><option value="local-cmake">Local CMake</option><option value="git">Git / FetchContent</option></select><button className="icon-button" onClick={onRemove} title="Remove library">×</button></div><div className="library-fields">{library.type === "git" ? <><Field label="Repository" value={library.repository} onChange={set("repository")} required /><Field label="Ref" value={library.ref} onChange={set("ref")} required /><Field label="Checkout directory" value={library.checkoutDir} onChange={set("checkoutDir")} required /></> : library.type === "local-cmake" ? <Field label="CMake root" value={library.path} onChange={set("path")} required /> : <Field label="Static archive" value={library.artifact} onChange={set("artifact")} required />}<Field label="CMake target" value={library.target} onChange={set("target")} /><Field label="Include directories" value={library.includeDirs} onChange={set("includeDirs")} /></div></div>; }

export default App;
