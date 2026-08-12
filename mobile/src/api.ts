import { Directory, File, Paths } from "expo-file-system";

/** Where the local engine lives, as seen from this phone. Set in .env. */
export const API =
  process.env.EXPO_PUBLIC_API_URL?.replace(/\/+$/, "") || "http://127.0.0.1:8000";

export type Delivered = { download: string; filename: string };

async function detail(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data?.detail || data?.message || `Engine failed (HTTP ${res.status}).`;
  } catch {
    return `Engine failed (HTTP ${res.status}).`;
  }
}

export async function health(signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(`${API}/api/health`, { signal });
    return res.ok;
  } catch {
    return false;
  }
}

/** Ask the engine for a link download, then stream it straight to disk —
 *  never buffer a whole video in JS memory. */
async function pull(job: Delivered): Promise<File> {
  const dir = new Directory(Paths.cache, "transcripe");
  if (!dir.exists) dir.create({ intermediates: true });
  const dest = new File(dir, job.filename);
  if (dest.exists) dest.delete();
  await File.downloadFileAsync(`${API}${job.download}`, dest, { idempotent: true });
  return dest;
}

export async function convertUrl(
  input: { url: string; format: string; useBrowserCookies: boolean },
  signal?: AbortSignal
): Promise<File> {
  const res = await fetch(`${API}/api/convert/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, deliver: "link" }),
    signal
  });
  if (!res.ok) throw new Error(await detail(res));
  return pull((await res.json()) as Delivered);
}

export async function convertFile(
  input: { uri: string; name: string; mimeType?: string; format: string },
  signal?: AbortSignal
): Promise<File> {
  const body = new FormData();
  // React Native's FormData takes this {uri,name,type} shape for file parts.
  body.append("file", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType || "application/octet-stream"
  } as unknown as Blob);
  body.append("targetFormat", input.format);
  body.append("deliver", "link");

  const res = await fetch(`${API}/api/convert/file`, {
    method: "POST",
    body,
    signal
  });
  if (!res.ok) throw new Error(await detail(res));
  return pull((await res.json()) as Delivered);
}
