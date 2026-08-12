# Transcripe — phone app

The studio, on your phone. It has no engines of its own: it picks a file or a
link, hands the job to the Transcripe engine running on your laptop, and saves
the result back to your phone. Nothing leaves your Wi-Fi.

Built with Expo SDK 54 — runs in **Expo Go**, no Xcode or Android Studio needed.

---

## Run it in three steps

**1 — Start the engine on your laptop** (from the repo root):

```bash
TRANSCRIPE_HOST=0.0.0.0 python server.py
```

`0.0.0.0` is the part that matters: the default `127.0.0.1` is only reachable
by the laptop itself, so your phone would see nothing.

**2 — Tell the app where the engine is.** Find your laptop's LAN address:

```bash
ip -4 addr | grep -v 127.0.0.1 | grep inet     # Linux
ipconfig getifaddr en0                          # macOS
```

Copy `.env.example` to `.env` and fill in that address:

```
EXPO_PUBLIC_API_URL=http://192.168.0.105:8000
```

Keep the `http://` and the `:8000`. This is the only value you need to fill in;
everything else is already configured.

**3 — Start the app:**

```bash
npm install     # first time only
npx expo start
```

Scan the QR code with **Expo Go** (Android) or the **Camera app** (iOS). Phone
and laptop must be on the same Wi-Fi.

---

## Reading the app

The dot next to the wordmark is the engine heartbeat, polled every 15 seconds:

| Dot | Meaning |
| --- | --- |
| green — *engine on* | the app can reach your laptop; convert away |
| red — *engine off* | engine not running, wrong IP in `.env`, different Wi-Fi, or a firewall blocking port 8000 |

**From my phone** picks a video or photo from the camera roll (or any file via
*Browse files instead*) and converts it — audio, video, and image formats.
Anything else (PDF, DOCX, archives, 3D models) belongs to the desktop CLI, and
the app says so instead of pretending.

**From a link** takes a YouTube / TikTok / Instagram / X / Spotify URL and
fetches it as video or audio. Switching to that tab offers whatever link is on
your clipboard.

Results land in the app's cache and open the native share sheet, so you can
save to Files/Photos or send them onward.

---

## Notes

- Files are streamed to disk as they download, so a large video never has to
  fit in the app's memory.
- The engine hands back a one-shot download link that expires after 15 minutes
  and dies on first use.
- `npm run web` opens the same app in a browser — handy for a quick look, but
  file picking and downloads behave differently there than on a real phone.
- Changing Wi-Fi networks usually changes your laptop's IP; update `.env` and
  restart `npx expo start` when the dot goes red.
