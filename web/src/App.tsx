import {
  ArrowRight,
  Check,
  Clipboard,
  Copy,
  Download,
  Plus,
  RotateCcw,
  X
} from "lucide-react";
import {
  ChangeEvent,
  DragEvent,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { CleanHeroBackground } from "./components/CleanHeroBackground";
import { services, Service } from "./data/services";

/* ── Format knowledge ────────────────────────────────────────────────────── */

const AUDIO_EXTS = ["mp3", "wav", "m4a", "flac", "aac", "ogg", "opus", "wma"];
const VIDEO_EXTS = ["mp4", "mkv", "mov", "webm", "avi", "3gp", "flv", "wmv"];
const IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"];

type Kind = "audio" | "video" | "image" | "other";

function kindOf(ext: string): Kind {
  if (AUDIO_EXTS.includes(ext)) return "audio";
  if (VIDEO_EXTS.includes(ext)) return "video";
  if (IMAGE_EXTS.includes(ext)) return "image";
  return "other";
}

/* Web engine targets per input kind; "other" falls back to the CLI. */
const TARGETS: Record<Exclude<Kind, "other">, { main: string[]; audio?: string[] }> = {
  audio: { main: ["mp3", "wav", "m4a", "flac", "ogg", "opus"] },
  video: { main: ["mp4", "webm", "mov", "mkv"], audio: ["mp3", "wav", "m4a"] },
  image: { main: ["png", "jpg", "webp", "bmp", "tiff"] }
};

const URL_TARGETS = ["mp4", "mp3", "m4a", "wav", "flac"];

const PLATFORMS: Array<[string[], string]> = [
  [["youtube.com", "youtu.be"], "YouTube"],
  [["instagram.com", "instagr.am"], "Instagram"],
  [["tiktok.com"], "TikTok"],
  [["twitter.com", "x.com"], "Twitter / X"],
  [["spotify.com", "spoti.fi"], "Spotify"],
  [["soundcloud.com"], "SoundCloud"],
  [["reddit.com", "redd.it"], "Reddit"],
  [["vimeo.com"], "Vimeo"],
  [["twitch.tv"], "Twitch"],
  [["facebook.com", "fb.watch"], "Facebook"],
  [["threads.net"], "Threads"],
  [["bsky.app"], "Bluesky"],
  [["pinterest.com", "pin.it"], "Pinterest"],
  [["snapchat.com"], "Snapchat"],
  [["dailymotion.com", "dai.ly"], "DailyMotion"],
  [["rumble.com"], "Rumble"],
  [["bilibili.com", "b23.tv"], "Bilibili"],
  [["music.apple.com"], "Apple Music"],
  [["bandcamp.com"], "Bandcamp"],
  [["t.me", "telegram.org"], "Telegram"]
];

function detectPlatform(url: string): string | null {
  const lower = url.trim().toLowerCase();
  if (!lower) return null;
  for (const [hosts, name] of PLATFORMS) {
    if (hosts.some((h) => lower.includes(h))) return name;
  }
  return lower.startsWith("http") ? "direct link" : null;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${(bytes / 1_073_741_824).toFixed(2)} GB`;
}

function extOf(name: string) {
  const parts = name.split("?")[0].split(".");
  return parts.length > 1 ? parts.pop()!.toLowerCase() : "";
}

/* Swap the example filename in a docs command for the user's actual file. */
function personalize(command: string, filename: string) {
  return command.replace(/(?<=\s)[\w.-]+\.[a-z0-9]{1,5}(?=\s|$)/i, filename);
}

function filenameFromDisposition(header: string | null): string {
  if (!header) return "";
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      /* fall through to the plain filename */
    }
  }
  const plain = header.match(/filename=["']?([^"';]+)["']?/i);
  return plain?.[1]?.trim() ?? "";
}

function triggerDownload(url: string, name: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

/* ── Small hooks ─────────────────────────────────────────────────────────── */

function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>(0);
  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1800);
    });
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [copied, copy];
}

/* ── Types ───────────────────────────────────────────────────────────────── */

type Entry = { id: string; file: File; ext: string };
type OutFile = { name: string; url: string };
type Phase = "idle" | "working" | "done";
type Mode = "file" | "url";

/* ── App ─────────────────────────────────────────────────────────────────── */

export function App() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [mode, setMode] = useState<Mode>("file");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [mediaUrl, setMediaUrl] = useState("");
  const [target, setTarget] = useState("");
  const [useCookies, setUseCookies] = useState(true);

  const [phase, setPhase] = useState<Phase>("idle");
  const [statusLabel, setStatusLabel] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [outputs, setOutputs] = useState<OutFile[]>([]);

  const [isDragging, setIsDragging] = useState(false);
  const [pipCopied, copyPip] = useCopy();
  const [termCopied, copyTerm] = useCopy();
  const [cmdCopied, copyCmd] = useCopy();

  const platform = useMemo(() => detectPlatform(mediaUrl), [mediaUrl]);

  const kind: Kind | null = entries.length ? kindOf(entries[0].ext) : null;
  const cliService: Service | null = useMemo(() => {
    if (kind !== "other" || !entries.length) return null;
    const ext = entries[0].ext;
    return services.find((s) => s.inputs.includes(ext)) ?? null;
  }, [kind, entries]);

  /* Elapsed-seconds ticker while the engine works. */
  useEffect(() => {
    if (phase !== "working") return;
    setElapsed(0);
    const t = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [phase]);

  /* Release result blobs when they're replaced. */
  useEffect(
    () => () => outputs.forEach((o) => URL.revokeObjectURL(o.url)),
    [outputs]
  );

  const resetResult = () => {
    setOutputs([]);
    setPhase("idle");
    setError("");
  };

  const addFiles = (list: FileList | File[]) => {
    const next = Array.from(list).map((file, i) => ({
      id: `${Date.now()}-${i}-${file.name}`,
      file,
      ext: extOf(file.name)
    }));
    if (!next.length) return;
    setEntries((prev) => {
      const merged = [...prev, ...next];
      const k = kindOf(merged[0].ext);
      if (k !== "other") {
        const opts = TARGETS[k];
        setTarget((t) =>
          [...opts.main, ...(opts.audio ?? [])].includes(t) ? t : opts.main[0]
        );
      }
      return merged;
    });
    resetResult();
  };

  const onFileInput = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) addFiles(e.target.files);
    e.target.value = "";
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  };

  const dragProps = {
    onDragOver: (e: DragEvent) => {
      e.preventDefault();
      setIsDragging(true);
    },
    onDragLeave: () => setIsDragging(false),
    onDrop
  };

  const removeEntry = (id: string) => {
    setEntries((prev) => prev.filter((f) => f.id !== id));
    resetResult();
  };

  const pasteFromClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      const match = text?.match(/https?:\/\/\S+/i);
      if (match) setMediaUrl(match[0]);
      else if (text?.trim()) setMediaUrl(text.trim());
    } catch {
      /* clipboard permission denied — user can type the link */
    }
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    setError("");
    if (m === "url") {
      setTarget((t) => (URL_TARGETS.includes(t) ? t : "mp4"));
      if (!mediaUrl) pasteFromClipboard();
    } else if (entries.length && kind && kind !== "other") {
      const opts = TARGETS[kind];
      setTarget((t) =>
        [...opts.main, ...(opts.audio ?? [])].includes(t) ? t : opts.main[0]
      );
    }
  };

  /* ── Conversion ──────────────────────────────────────────────────────── */

  const failDetail = async (res: Response) => {
    const data = await res.json().catch(() => null);
    return data?.detail || data?.message || `Engine failed (HTTP ${res.status}).`;
  };

  const runUrlConvert = async (signal: AbortSignal): Promise<OutFile[]> => {
    setStatusLabel(`Fetching ${platform ?? "link"} → .${target}`);
    const res = await fetch("/api/convert/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: mediaUrl.trim(),
        format: target || "mp4",
        useBrowserCookies: useCookies
      }),
      signal
    });
    if (!res.ok) throw new Error(await failDetail(res));
    const blob = await res.blob();
    const name =
      filenameFromDisposition(res.headers.get("content-disposition")) ||
      `transcripe.${target || "mp4"}`;
    return [{ name, url: URL.createObjectURL(blob) }];
  };

  const runFileConvert = async (signal: AbortSignal): Promise<OutFile[]> => {
    const results: OutFile[] = [];
    for (let i = 0; i < entries.length; i++) {
      const { file } = entries[i];
      const prefix = entries.length > 1 ? `${i + 1} of ${entries.length} · ` : "";
      setStatusLabel(`${prefix}${file.name} → .${target}`);
      const body = new FormData();
      body.append("file", file);
      body.append("targetFormat", target);
      const res = await fetch("/api/convert/file", { method: "POST", body, signal });
      if (!res.ok) throw new Error(`${file.name}: ${await failDetail(res)}`);
      const blob = await res.blob();
      const name =
        filenameFromDisposition(res.headers.get("content-disposition")) ||
        `${file.name.replace(/\.[^.]+$/, "")}.${target}`;
      results.push({ name, url: URL.createObjectURL(blob) });
    }
    return results;
  };

  const convert = async () => {
    setError("");
    setPhase("working");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const results =
        mode === "url"
          ? await runUrlConvert(controller.signal)
          : await runFileConvert(controller.signal);
      setOutputs(results);
      setPhase("done");
      results.forEach((r) => triggerDownload(r.url, r.name));
    } catch (err: unknown) {
      if ((err as Error)?.name === "AbortError") {
        setPhase("idle");
        return;
      }
      setError((err as Error)?.message || "Could not reach the local engine.");
      setPhase("idle");
    } finally {
      abortRef.current = null;
    }
  };

  const cancel = () => abortRef.current?.abort();

  const startOver = () => {
    setEntries([]);
    setMediaUrl("");
    resetResult();
  };

  const canConvert =
    phase !== "working" &&
    (mode === "url"
      ? mediaUrl.trim().length > 0
      : entries.length > 0 && kind !== "other" && !!target);

  const minutes = Math.floor(elapsed / 60);
  const seconds = String(elapsed % 60).padStart(2, "0");

  /* ── Render ──────────────────────────────────────────────────────────── */

  const targetChips = () => {
    if (mode === "url") {
      return (
        <div className="opt">
          <span className="opt-label">Save as</span>
          <div className="chips">
            {URL_TARGETS.map((fmt) => (
              <button
                key={fmt}
                className={`chip ${target === fmt ? "active" : ""}`}
                onClick={() => setTarget(fmt)}
              >
                .{fmt}
              </button>
            ))}
          </div>
        </div>
      );
    }
    if (!kind || kind === "other") return null;
    const opts = TARGETS[kind];
    return (
      <div className="opt">
        <span className="opt-label">Convert to</span>
        <div className="chips">
          {opts.main.map((fmt) => (
            <button
              key={fmt}
              className={`chip ${target === fmt ? "active" : ""}`}
              onClick={() => setTarget(fmt)}
            >
              .{fmt}
            </button>
          ))}
          {opts.audio && (
            <>
              <span className="chip-divider">audio only</span>
              {opts.audio.map((fmt) => (
                <button
                  key={fmt}
                  className={`chip ${target === fmt ? "active" : ""}`}
                  onClick={() => setTarget(fmt)}
                >
                  .{fmt}
                </button>
              ))}
            </>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="page">
      <CleanHeroBackground />

      <header className="site-head shell reveal d0">
        <a className="wordmark" href="#top">
          Transcripe<span className="dot">.</span>
        </a>
        <nav className="site-nav">
          <a className="nav-link" href="#engines">
            Engines
          </a>
          <a
            className="nav-link"
            href="https://github.com/abdulrahmanJAlabbed/Transcripe"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
          <button
            className={`pip-chip ${pipCopied ? "copied" : ""}`}
            onClick={() => copyPip("pip install transcripe")}
          >
            {pipCopied ? "copied ✓" : "$ pip install transcripe"}
          </button>
        </nav>
      </header>

      <main>
        <section className="hero shell" id="top">
          <p className="eyebrow reveal d1">Local · Private · Open source</p>
          <h1 className="reveal d2">
            Every file, <em>quietly</em> transformed.
          </h1>
          <p className="lede reveal d3">
            Transcribe lectures, pull reels, merge PDFs, vectorize logos —
            sixteen engines that run on your machine and answer to no cloud.
          </p>

          <div className="card reveal d4">
            <div className="seg" data-mode={mode} role="tablist">
              <span className="seg-thumb" aria-hidden="true" />
              <button
                role="tab"
                aria-selected={mode === "file"}
                onClick={() => switchMode("file")}
              >
                Upload files
              </button>
              <button
                role="tab"
                aria-selected={mode === "url"}
                onClick={() => switchMode("url")}
              >
                From a link
              </button>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              multiple
              onChange={onFileInput}
              style={{ display: "none" }}
            />

            {mode === "file" ? (
              <div className="panel" key="file">
                {entries.length === 0 ? (
                  <div
                    className={`dropzone ${isDragging ? "is-dragging" : ""}`}
                    onClick={() => fileInputRef.current?.click()}
                    onKeyDown={(e) =>
                      e.key === "Enter" && fileInputRef.current?.click()
                    }
                    role="button"
                    tabIndex={0}
                    aria-label="Add files to convert"
                    {...dragProps}
                  >
                    <span className="drop-plus">
                      <Plus size={20} strokeWidth={1.8} />
                    </span>
                    <div>
                      <div className="drop-title">Drop files here</div>
                      <div className="drop-sub">or click to browse — audio, video, images</div>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="file-stack">
                      {entries.map((entry) => (
                        <div className="file-row" key={entry.id}>
                          <span className="ext-badge">{entry.ext || "file"}</span>
                          <span className="file-name">{entry.file.name}</span>
                          <span className="file-size">
                            {formatBytes(entry.file.size)}
                          </span>
                          <button
                            className="row-x"
                            onClick={() => removeEntry(entry.id)}
                            aria-label={`Remove ${entry.file.name}`}
                          >
                            <X size={14} />
                          </button>
                        </div>
                      ))}
                    </div>

                    <button
                      className={`add-more ${isDragging ? "is-dragging" : ""}`}
                      onClick={() => fileInputRef.current?.click()}
                      {...dragProps}
                    >
                      <Plus size={14} /> Add more files
                    </button>

                    {cliService ? (
                      <div className="cli-hint">
                        <span className="cli-hint-title">
                          {cliService.title} lives in the CLI
                        </span>
                        <span className="cli-hint-note">
                          .{entries[0].ext} isn&apos;t handled in the browser yet —
                          this one-liner does it locally:
                        </span>
                        <div className="cmd-line">
                          <code>
                            {personalize(cliService.command, entries[0].file.name)}
                          </code>
                          <button
                            className={`cmd-copy ${cmdCopied ? "copied" : ""}`}
                            onClick={() =>
                              copyCmd(
                                personalize(
                                  cliService.command,
                                  entries[0].file.name
                                )
                              )
                            }
                            aria-label="Copy command"
                          >
                            {cmdCopied ? <Check size={13} /> : <Copy size={13} />}
                          </button>
                        </div>
                      </div>
                    ) : (
                      targetChips()
                    )}
                  </>
                )}
              </div>
            ) : (
              <div className="panel" key="url">
                <div className="url-wrap">
                  <input
                    className="url-input"
                    type="url"
                    placeholder="Paste a link — YouTube, Instagram, TikTok, X…"
                    value={mediaUrl}
                    onChange={(e) => {
                      setMediaUrl(e.target.value);
                      if (phase === "done") resetResult();
                    }}
                  />
                  <button className="paste-btn" onClick={pasteFromClipboard}>
                    <Clipboard size={13} /> Paste
                  </button>
                </div>

                {platform ? (
                  <div className="detect">{platform} detected</div>
                ) : (
                  <p className="hint">
                    Works with 1,000+ sites — reels without watermarks, shorts,
                    clips, full tracks.
                  </p>
                )}

                {mediaUrl.trim() && (
                  <>
                    {targetChips()}
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={useCookies}
                        onChange={(e) => setUseCookies(e.target.checked)}
                      />
                      Use my browser&apos;s cookies for private or age-gated links
                      — never stored
                    </label>
                  </>
                )}
              </div>
            )}

            {error && (
              <div className="err-banner" role="alert">
                <span>{error}</span>
                <button onClick={() => setError("")} aria-label="Dismiss">
                  <X size={14} />
                </button>
              </div>
            )}

            {phase === "working" ? (
              <div className="working">
                <div className="working-row">
                  <span className="working-label">{statusLabel}</span>
                  <span className="working-time">
                    {minutes}:{seconds}
                  </span>
                </div>
                <div className="track">
                  <div className="track-slide" />
                </div>
                <button className="cancel-btn" onClick={cancel}>
                  Cancel
                </button>
              </div>
            ) : phase === "done" && outputs.length > 0 ? (
              <div className="done">
                <div className="done-row">
                  <span className="done-check">
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M4 12.5l5.5 5.5L20 6.5" />
                    </svg>
                  </span>
                  <div className="done-text">
                    <div className="done-title">{outputs[0].name}</div>
                    <div className="done-sub">
                      {outputs.length > 1
                        ? `and ${outputs.length - 1} more — downloads started`
                        : "download started"}
                    </div>
                  </div>
                </div>
                <div className="done-actions">
                  <button
                    className="ghost accent"
                    onClick={() =>
                      outputs.forEach((o) => triggerDownload(o.url, o.name))
                    }
                  >
                    <Download size={15} /> Save again
                  </button>
                  <button className="ghost" onClick={startOver}>
                    <RotateCcw size={14} /> Convert another
                  </button>
                </div>
              </div>
            ) : (
              <button className="cta" disabled={!canConvert} onClick={convert}>
                {mode === "url"
                  ? `Fetch & convert${target ? ` to .${target}` : ""}`
                  : kind === "other"
                  ? "Use the CLI for this format"
                  : `Convert${target ? ` to .${target}` : ""}`}
                <ArrowRight className="arrow" size={17} />
              </button>
            )}
          </div>

          <p className="trust reveal d5">
            processed in memory · streamed back · <b>deleted immediately</b>
          </p>
        </section>

        <section className="engines shell" id="engines">
          <div className="sec-head">
            <h2>
              Sixteen engines, <em>one</em> keystroke each.
            </h2>
            <p>
              The studio above covers media and images. Everything here ships in
              the CLI — same machine, same privacy, more muscle.
            </p>
          </div>

          <div className="engine-grid">
            {services.map((s) => {
              const Icon = s.icon;
              return (
                <div className="engine-cell" key={s.id}>
                  <div className="engine-top">
                    <Icon size={17} strokeWidth={1.8} />
                    <span className="engine-cat">{s.category}</span>
                  </div>
                  <div className="engine-title">{s.title}</div>
                  <p className="engine-sum">{s.summary}</p>
                  <div className="engine-io">
                    {s.inputs.slice(0, 3).join(" ")}
                    {s.inputs.length > 3 ? ` +${s.inputs.length - 3}` : ""}
                    <span className="io-arrow">→</span>
                    {s.outputs.slice(0, 3).join(" ")}
                    {s.outputs.length > 3 ? ` +${s.outputs.length - 3}` : ""}
                  </div>
                </div>
              );
            })}
            <a
              className="engine-cell more"
              href="https://github.com/abdulrahmanJAlabbed/Transcripe/issues"
              target="_blank"
              rel="noreferrer"
            >
              <div className="engine-title">Missing a format? →</div>
              <p className="engine-sum">
                Open an issue and it&apos;ll likely land as engine seventeen.
              </p>
            </a>
          </div>
        </section>

        <section className="cli-band shell">
          <div className="term">
            <button
              className={`term-copy ${termCopied ? "copied" : ""}`}
              onClick={() => copyTerm("pip install transcripe")}
            >
              {termCopied ? <Check size={12} /> : <Copy size={12} />}
              {termCopied ? "copied" : "copy"}
            </button>
            <div className="term-lines">
              <span>
                <span className="c"># the whole studio, one install</span>
              </span>
              <span>
                <span className="p">$ </span>pip install transcripe
              </span>
              <span>
                <span className="p">$ </span>transcripe media transcribe
                lecture.mp4 --srt
              </span>
              <span>
                <span className="p">$ </span>transcripe download
                https://instagram.com/reel/… --to mp4
              </span>
            </div>
          </div>
          <p className="cli-caption">
            Batch folders, watch modes, language packs —{" "}
            <a
              href="https://github.com/abdulrahmanJAlabbed/Transcripe"
              target="_blank"
              rel="noreferrer"
            >
              the README has the full tour
            </a>
            .
          </p>
        </section>
      </main>

      <footer className="site-foot">
        <div className="foot-inner shell">
          <span className="foot-brand">Transcripe.</span>
          <span>Runs where your files live.</span>
          <nav className="foot-links">
            <a
              href="https://github.com/abdulrahmanJAlabbed/Transcripe"
              target="_blank"
              rel="noreferrer"
            >
              GitHub
            </a>
            <a
              href="https://pypi.org/project/transcripe/"
              target="_blank"
              rel="noreferrer"
            >
              PyPI
            </a>
          </nav>
        </div>
      </footer>
    </div>
  );
}

export default App;
