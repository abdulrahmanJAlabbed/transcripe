/**
 * Conversions the visitor's own machine can do.
 *
 * The browser is not a poor relation of the engine here — on this hardware it
 * encodes 720p H.264 at ~350 fps through WebCodecs, comfortably faster than a
 * small server, and the file never leaves the device. So: try locally first,
 * fall back to the engine when the browser can't (HEIC decoding in Chrome, mp3
 * encoding, link downloads, Whisper).
 */

const IMAGE_OUT: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp"
};

/* Formats every engine-capable browser can decode. HEIC is deliberately absent:
   only Safari decodes it, so it goes to the engine where Pillow handles it. */
const IMAGE_IN = ["png", "jpg", "jpeg", "webp", "gif", "bmp"];

export type LocalResult = { blob: Blob; name: string };

export function canConvertLocally(sourceExt: string, target: string): boolean {
  if (typeof createImageBitmap === "undefined") return false;
  if (typeof OffscreenCanvas === "undefined") return false;
  return IMAGE_IN.includes(sourceExt.toLowerCase()) && target in IMAGE_OUT;
}

/* Sources that arrived without generation loss. Re-compressing one of these
   into a lossy target would throw away detail for nothing. */
const LOSSLESS_SOURCES = ["png", "bmp", "gif", "webp", "tif", "tiff"];

/** Encoder quality, matched to the engine's policy so both paths produce the
 *  same picture. Measured in Chrome: WebP at 1.0 is bit-exact (zero pixel
 *  error), 0.95 is not; PNG is always lossless. */
function encodeQuality(sourceExt: string, target: string): number {
  if (target === "png") return 1;
  if (target === "webp" && LOSSLESS_SOURCES.includes(sourceExt.toLowerCase())) {
    return 1; // lossless WebP
  }
  return 0.95;
}

/** Decode → repaint → re-encode, all in the page. Throws if the browser can't
 *  read the file, which is the caller's cue to fall back to the engine. */
export async function convertImageLocally(
  file: File,
  target: string
): Promise<LocalResult> {
  const type = IMAGE_OUT[target];
  if (!type) throw new Error(`no local encoder for .${target}`);

  // createImageBitmap honours the EXIF orientation tag, so a portrait phone
  // photo comes out upright — same as the engine does with exif_transpose.
  const bitmap = await createImageBitmap(file);
  try {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context");

    // JPEG has no alpha; without this, transparent pixels come out black.
    if (type === "image/jpeg") {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    ctx.drawImage(bitmap, 0, 0);

    const quality = encodeQuality(extensionOf(file.name), target);
    const blob = await canvas.convertToBlob({ type, quality });
    if (!blob || blob.size === 0) throw new Error("encoder produced nothing");
    return {
      blob,
      name: `${file.name.replace(/\.[^.]+$/, "")}.${target}`
    };
  } finally {
    bitmap.close();
  }
}

function extensionOf(name: string): string {
  const parts = name.split("?")[0].split(".");
  return parts.length > 1 ? parts.pop()!.toLowerCase() : "";
}
