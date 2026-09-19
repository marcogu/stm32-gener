import { useEffect, useMemo, useRef, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { invoke } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import { configurationToForm, emptyLibrary, formToConfiguration, projectOutputDirectory } from "./configuration";
import type { Configuration, Library, ProjectForm } from "./configuration";
import "./App.css";

function App() {
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState<{ title: string; message: string } | null>(null);
  const errorBanner = useRef<HTMLDivElement>(null);
  const contentPanel = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState(false);
  const [diff, setDiff] = useState("");
  const [project, setProject] = useState<ProjectForm>({ name: "demo_app", parentDir: "", sourceDirs: "src", includeDirs: "inc" });
  const [environment, setEnvironment] = useState({ id: "", rootDir: "", toolchain: "", cubemx: "" });
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [importedConfig, setImportedConfig] = useState<Configuration>();

  useEffect(() => {
    getVersion().then(setAppVersion).catch(() => setAppVersion(null));
  }, []);

  const projectOutputDir = projectOutputDirectory(project);

  useEffect(() => {
    contentPanel.current?.scrollTo({ top: 0, left: 0 });
  }, [step]);

  useEffect(() => {
    if (error) {
      errorBanner.current?.focus({ preventScroll: true });
      errorBanner.current?.scrollIntoView({ block: "nearest" });
    }
  }, [error]);

  const config = useMemo(() => formToConfiguration(project, environment, libraries, importedConfig), [environment, importedConfig, libraries, project]);

  useEffect(() => { setDiff(""); }, [config]);

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
        if (library.type === "local-static" && !library.artifact.trim() && !library.original?.source.artifactByEnvironment?.[environment.id]) return `${label}: static archive is required.`;
        if (library.type === "local-cmake" && !library.path.trim()) return `${label}: CMake root is required.`;
        if (library.type === "git" && !library.repository.trim()) return `${label}: repository is required.`;
        if (library.type === "git" && !library.ref.trim()) return `${label}: ref is required.`;
        if (library.type === "git" && !library.checkoutDir.trim()) return `${label}: checkout directory is required.`;
        if (library.type === "git" && /(^[\\/]|^[A-Za-z]:|(^|[\\/])\.\.([\\/]|$)|[;\r\n\0])/.test(library.cmakeSubdir.trim())) return `${label}: SOURCE_SUBDIR must be a relative path inside the repository, without '..'.`;
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
    setBusy(true);
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
    finally { setBusy(false); }
  }

  async function importConfiguration() {
    if (busy) return;
    setBusy(true);
    try {
      const selected = await open({ multiple: false, title: "Import configuration", filters: [{ name: "Configuration", extensions: ["json"] }] });
      if (!selected) return;
      setError(null);
      setStatus("Importing configuration…");
      const result = await invoke<{ config: Configuration; output: string }>("bridge", { request: { action: "import_config", path: selected } });
      const form = configurationToForm(result.config, result.output);
      setImportedConfig(result.config);
      setProject(form.project);
      setEnvironment(form.environment);
      setLibraries(form.libraries);
      setDiff("");
      setStep(0);
      setStatus("Configuration imported");
    } catch (error) { fail(errorMessage(error), step, "Import failed"); }
    finally { setBusy(false); }
  }

  async function exportConfiguration() {
    if (busy) return;
    setBusy(true);
    try {
      const selected = await save({ title: "Export configuration", defaultPath: `${project.name.trim().replace(/[^A-Za-z0-9_-]/g, "_") || "project"}.json`, filters: [{ name: "Configuration", extensions: ["json"] }] });
      if (!selected) return;
      setError(null);
      setStatus("Saving configuration…");
      await invoke("bridge", { request: { action: "export_config", path: selected, config, configDir: ".", output: projectOutputDir } });
      setStatus("Configuration saved");
    } catch (error) { fail(errorMessage(error), step, "Export failed"); }
    finally { setBusy(false); }
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
      const result = await invoke<{ diff: string; gitCommitted: boolean | null }>("bridge", { request: { action, config, configDir: ".", output: projectOutputDir } });
      setDiff(result.diff);
      setStatus(action === "generate" ? result.gitCommitted ? "Project generated · Git committed" : "Project generated · Git up to date" : "Preview updated");
      setStep(3);
    } catch (error) { fail(errorMessage(error), 3, action === "generate" ? "Generation failed" : "Preview failed"); }
    finally { setBusy(false); }
  }

  const updateProject = (key: keyof typeof project) => (event: React.ChangeEvent<HTMLInputElement>) => setProject({ ...project, [key]: event.target.value, ...(key === "name" || key === "parentDir" ? { importedRootDir: undefined } : {}) });
  const updateEnvironment = (key: keyof typeof environment) => (event: React.ChangeEvent<HTMLInputElement>) => setEnvironment({ ...environment, [key]: event.target.value });
  async function chooseProjectDirectory() {
    try {
      const selected = await open({ directory: true, multiple: false, title: "Select parent directory", defaultPath: project.parentDir || undefined });
      if (selected) setProject((value) => ({ ...value, parentDir: selected, importedRootDir: undefined }));
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
  async function chooseLibraryPath(index: number, field: "artifact" | "path") {
    const library = libraries[index];
    setBusy(true);
    try {
      const selected = field === "path"
        ? await open({ directory: true, multiple: false, title: "Select CMake root", defaultPath: library.path || undefined })
        : await open({ multiple: false, title: "Select static archive", defaultPath: library.artifact || undefined, filters: [{ name: "Static archive", extensions: ["a", "lib"] }] });
      if (selected && typeof selected === "string") {
        if (field === "path") {
          const found = await invoke<Record<string, unknown>>("bridge", { request: { action: "scan", kind: "library", path: selected } });
          if (found.hasCMake !== true) throw new Error("Selected CMake root does not contain CMakeLists.txt.");
          const name = typeof found.name === "string" ? found.name.trim() : "";
          const target = typeof found.target === "string" ? found.target.trim() : "";
          if (!name || !target) throw new Error("Could not determine a library name from CMakeLists.txt.");
          setLibraries((value) => value.map((item, itemIndex) => itemIndex === index ? { ...item, path: selected, name, target } : item));
          setStatus(`Detected library: ${name}`);
        } else {
          setLibraries((value) => value.map((item, itemIndex) => itemIndex === index ? { ...item, artifact: selected } : item));
        }
      }
    } catch (error) { fail(errorMessage(error), 2, field === "path" ? "Directory selection failed" : "File selection failed"); }
    finally { setBusy(false); }
  }

  async function chooseLibraryIncludeDirs(index: number) {
    const library = libraries[index];
    try {
      const selected = await open({ directory: true, multiple: true, title: "Select include directories", defaultPath: library.includeDirs.split(/\r?\n/)[0]?.trim() || undefined });
      if (selected) {
        const paths = Array.isArray(selected) ? selected : [selected];
        setLibraries((value) => value.map((item, itemIndex) => {
          if (itemIndex !== index) return item;
          const existing = item.includeDirs.split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean);
          return { ...item, includeDirs: [...new Set([...existing, ...paths])].join("\n") };
        }));
      }
    } catch (error) { fail(errorMessage(error), 2, "Directory selection failed"); }
  }

  return <main className="app-shell">
    <header className="topbar"><div className="brand"><img className="brand-mark" src="/icon.svg" alt="" /><span className="brand-name">CMake Forge</span><span className="app-version" aria-label="Application version">{appVersion ? `v${appVersion}` : "Version unavailable"}</span></div><div className="topbar-actions"><span className={error ? "status status-error" : "status"} role="status"><i className="status-dot" />{status}</span><button className="secondary" disabled={busy} onClick={importConfiguration}>Import config</button><button className="secondary" disabled={busy} onClick={exportConfiguration}>Export config</button></div></header>
    <fieldset className="workspace" disabled={busy}>
      <aside className="sidebar"><div className="eyebrow">PROJECT BUILDER</div><nav>{["Project", "Environment", "Libraries", "Review"].map((label, index) => <button key={label} className={step === index ? "nav-item active" : "nav-item"} onClick={() => setStep(index)}><span className="nav-number">0{index + 1}</span>{label}</button>)}</nav><div className="sidebar-foot"><span className="tiny-label">SCHEMA</span><strong>v1</strong><span className="tiny-label">TARGET</span><strong>{environment.id || "Not selected"}</strong></div></aside>
      <section className="content" ref={contentPanel}><div className="page-heading"><div><span className="kicker">STEP 0{step + 1} / 04</span><h1>{["Project identity", "Build environment", "Library sources", "Review & generate"][step]}</h1><p>{["Define the application directory and source layout.", "Connect an existing CubeMX environment and ARM toolchain.", "Choose local artifacts or Git dependencies per library.", "Inspect the generated files before writing them to disk."][step]}</p></div><div className="progress"><span style={{ width: `${((step + 1) / 4) * 100}%` }} /></div></div>
        {error && <div className="error-banner" role="alert" ref={errorBanner} tabIndex={-1}><strong>{error.title}</strong><span>{error.message}</span><button type="button" onClick={() => { setError(null); setStatus("Ready"); }} aria-label="Dismiss error">×</button></div>}
        {step === 0 && <Panel title="Application" note="A folder named after the project is created inside the selected parent directory. Missing source and include folders are created inside it."><div className="form-grid"><Field label="Project name" value={project.name} onChange={updateProject("name")} required /><DirectoryField label="Parent directory" value={project.parentDir} onChange={updateProject("parentDir")} onBrowse={chooseProjectDirectory} placeholder="/path/to/workspace" required /><Field label="Source directories" value={project.sourceDirs} onChange={updateProject("sourceDirs")} /><Field label="Include directories" value={project.includeDirs} onChange={updateProject("includeDirs")} /><div className="field action-field"><label>Discovery</label><button className="secondary" onClick={() => scan("project")}>Scan project</button></div><div className="output-directory"><span>Project directory</span><code>{projectOutputDir || "Choose a parent directory"}</code>{project.importedRootDir && <small>Imported location. Change the name or parent directory to create a new project location.</small>}</div></div></Panel>}
        {step === 1 && <Panel title="Environment" note="Select the environment root; toolchain and CubeMX are detected automatically."><div className="form-grid"><DirectoryField label="Environment root" value={environment.rootDir} onChange={updateEnvironment("rootDir")} onBrowse={chooseEnvironmentRoot} placeholder="/path/to/envs/stm32f407vet6" required /><Field label="Environment ID" value={environment.id} onChange={updateEnvironment("id")} placeholder="stm32f407vet6" required /><Field label="Toolchain file" value={environment.toolchain} onChange={updateEnvironment("toolchain")} placeholder=".../cmake/arm-none-eabi.cmake" required /><Field label="CubeMX module (optional)" value={environment.cubemx} onChange={updateEnvironment("cubemx")} placeholder="Defaults to environment/cmake/stm32cubemx" /><div className="field action-field"><label>Detection</label><button className="secondary" onClick={() => scan("environment")}>Scan environment</button></div></div></Panel>}
        {step === 2 && <Panel title="Dependencies" note="Each entry is rendered as a CMake target. Git sources are fetched during configure."><div className="library-list">{libraries.map((lib, index) => <LibraryRow key={index} library={lib} onChange={(next) => setLibraries(libraries.map((item, itemIndex) => itemIndex === index ? next : item))} onRemove={() => setLibraries(libraries.filter((_, itemIndex) => itemIndex !== index))} onBrowse={(field) => chooseLibraryPath(index, field)} onBrowseIncludeDirs={() => chooseLibraryIncludeDirs(index)} />)}</div><button className="add-button" onClick={() => setLibraries([...libraries, emptyLibrary()])}>+ Add library</button></Panel>}
        {step === 3 && <Panel title="Generated files" note="Review the generated diff before writing it to the output directory."><div className="review-toolbar"><button className="secondary" disabled={busy} onClick={() => previewGeneration("preview")}>Refresh preview</button><button className="primary" disabled={busy} onClick={() => previewGeneration("generate")}>{busy ? "Working…" : "Generate project"}</button></div><pre className="diff">{diff || "No preview yet. Configure the project, then refresh preview."}</pre></Panel>}
        <footer className="actions"><button className="ghost" disabled={step === 0 || busy} onClick={() => setStep(step - 1)}>Back</button>{step < 3 ? <button className="primary" disabled={busy} onClick={() => { const message = validateStep(step); if (message) return fail(message, step); setError(null); setStatus("Ready"); setStep(step + 1); }}>Continue <span>→</span></button> : <button className="secondary" disabled={busy} onClick={() => previewGeneration("preview")}>Preview <span>↗</span></button>}</footer>
      </section>
    </fieldset>
  </main>;
}

function Panel({ title, note, children }: { title: string; note: string; children: React.ReactNode }) { return <section className="panel"><div className="panel-heading"><h2>{title}</h2><p>{note}</p></div>{children}</section>; }
function Field({ label, value, onChange, placeholder, hint, required = false }: { label: string; value: string; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; placeholder?: string; hint?: string; required?: boolean }) { return <div className="field"><label>{label}{required && <span className="required-mark"> *</span>}</label><input value={value} onChange={onChange} placeholder={placeholder} required={required} aria-required={required} aria-label={label} />{hint && <small>{hint}</small>}</div>; }
function DirectoryField({ label, value, onChange, onBrowse, placeholder, required = false }: { label: string; value: string; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; onBrowse: () => void; placeholder?: string; required?: boolean }) { return <div className="field"><label>{label}{required && <span className="required-mark"> *</span>}</label><div className="path-control"><input value={value} onChange={onChange} onClick={onBrowse} placeholder={placeholder} aria-label={`${label} path`} required={required} aria-required={required} /><button type="button" className="browse-button" onClick={onBrowse} title={`Choose ${label.toLowerCase()}`} aria-label={`Choose ${label.toLowerCase()}`}><span className="folder-icon" aria-hidden="true" /></button></div></div>; }
function PathField({ label, value, onChange, onBrowse, placeholder, required = false }: { label: string; value: string; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; onBrowse: () => void; placeholder?: string; required?: boolean }) { return <div className="field"><label>{label}{required && <span className="required-mark"> *</span>}</label><div className="path-control"><input value={value} onChange={onChange} placeholder={placeholder} aria-label={`${label} path`} required={required} aria-required={required} /><button type="button" className="browse-button" onClick={onBrowse} title={`Choose ${label.toLowerCase()}`} aria-label={`Choose ${label.toLowerCase()}`}><span className="folder-icon" aria-hidden="true" /></button></div></div>; }
function MultiPathField({ label, value, onChange, onBrowse }: { label: string; value: string; onChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void; onBrowse: () => void }) {
  return <div className="field"><label>{label}</label><div className="path-control multi-path-control"><textarea value={value} onChange={onChange} rows={3} placeholder="/path/to/include" aria-label={`${label} paths`} /><button type="button" className="browse-button" onClick={onBrowse} title={`Choose ${label.toLowerCase()}`} aria-label={`Choose ${label.toLowerCase()}`}><span className="folder-icon" aria-hidden="true" /></button></div></div>;
}
function LibraryRow({ library, onChange, onRemove, onBrowse, onBrowseIncludeDirs }: { library: Library; onChange: (next: Library) => void; onRemove: () => void; onBrowse: (field: "artifact" | "path") => void; onBrowseIncludeDirs: () => void }) {
  const set = (key: keyof Library) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => onChange({ ...library, [key]: event.target.value });
  const variants = library.original?.source.artifactByEnvironment;
  return <div className="library-row">
    <div className="library-row-top"><input className="library-name" value={library.name} onChange={set("name")} placeholder="library name *" required aria-required="true" /><select value={library.type} onChange={set("type")}><option value="local-static">Local static</option><option value="local-cmake">Local CMake</option><option value="git">Git / FetchContent</option></select><button className="icon-button" onClick={onRemove} title="Remove library">×</button></div>
    <div className="library-fields">
      {library.type === "git" ? <>
        <Field label="Repository" value={library.repository} onChange={set("repository")} required />
        <Field label="Ref" value={library.ref} onChange={set("ref")} required />
        <Field label="Checkout directory" value={library.checkoutDir} onChange={set("checkoutDir")} required />
        <Field label="SOURCE_SUBDIR (optional)" value={library.cmakeSubdir} onChange={set("cmakeSubdir")} placeholder="e.g. cmake/library" hint="CMake directory relative to the repository root. Leave blank to use the root (.)." />
      </> : library.type === "local-cmake"
        ? <PathField label="CMake root" value={library.path} onChange={set("path")} onBrowse={() => onBrowse("path")} required />
        : <><PathField label={variants ? "Static archive (fallback)" : "Static archive"} value={library.artifact} onChange={set("artifact")} onBrowse={() => onBrowse("artifact")} required={!variants} />{variants && <div className="field"><small>Imported environment-specific archives are preserved and take precedence over the fallback.</small></div>}</>}
      <Field label="CMake target" value={library.target} onChange={set("target")} />
      <MultiPathField label="Include directories" value={library.includeDirs} onChange={set("includeDirs")} onBrowse={onBrowseIncludeDirs} />
    </div>
  </div>;
}

export default App;
