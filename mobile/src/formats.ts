/** Format + platform knowledge, ported from the web studio. */

export const AUDIO_EXTS = ["mp3", "wav", "m4a", "flac", "aac", "ogg", "opus", "wma"];
export const VIDEO_EXTS = ["mp4", "mkv", "mov", "webm", "avi", "3gp", "flv", "wmv"];
export const IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "heic"];

export type Kind = "audio" | "video" | "image" | "other";

export function kindOf(ext: string): Kind {
  const e = ext.toLowerCase();
  if (AUDIO_EXTS.includes(e)) return "audio";
  if (VIDEO_EXTS.includes(e)) return "video";
  if (IMAGE_EXTS.includes(e)) return "image";
  return "other";
}

/** `text` targets go to Whisper on the laptop rather than ffmpeg. */
export const TARGETS: Record<
  Exclude<Kind, "other">,
  { main: string[]; audio?: string[]; text?: string[] }
> = {
  audio: { main: ["mp3", "wav", "m4a", "flac", "ogg", "opus"], text: ["srt", "txt"] },
  video: {
    main: ["mp4", "webm", "mov", "mkv"],
    audio: ["mp3", "wav", "m4a"],
    text: ["srt", "txt"]
  },
  image: { main: ["png", "jpg", "webp", "bmp", "tiff"] }
};

export const TEXT_TARGETS = ["srt", "txt"];

export const URL_TARGETS = ["mp4", "mp3", "m4a", "wav", "flac"];

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

export function detectPlatform(url: string): string | null {
  const lower = url.trim().toLowerCase();
  if (!lower) return null;
  for (const [hosts, name] of PLATFORMS) {
    if (hosts.some((h) => lower.includes(h))) return name;
  }
  return lower.startsWith("http") ? "direct link" : null;
}

export function extOf(name: string): string {
  const parts = name.split("?")[0].split(".");
  return parts.length > 1 ? parts.pop()!.toLowerCase() : "";
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${(bytes / 1_073_741_824).toFixed(2)} GB`;
}

export function firstUrl(text: string): string | null {
  return text.match(/https?:\/\/\S+/i)?.[0] ?? null;
}
