import { useEffect, useRef } from "react";

/**
 * The signature: a waveform that walks left to right and collapses into
 * caption lines behind itself — sound turning into text, which is the whole
 * product in one image.
 *
 * Canvas, one rAF loop, no dependencies. The pointer bends the wave it passes
 * over, so the thing feels alive without asking for attention.
 */
export function HeroWave() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Deterministic "audio": layered sines, so every visit draws the same clip.
    const amplitudeAt = (i: number) =>
      0.34 * Math.sin(i * 0.19) +
      0.26 * Math.sin(i * 0.41 + 1.3) +
      0.18 * Math.sin(i * 0.83 + 0.7) +
      0.12 * Math.sin(i * 1.7 + 2.1);

    let width = 0;
    let height = 0;
    let dpr = 1;
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    const pointer = { x: -999, y: -999 };
    const onMove = (e: PointerEvent) => {
      const box = canvas.getBoundingClientRect();
      pointer.x = e.clientX - box.left;
      pointer.y = e.clientY - box.top;
    };
    const onLeave = () => {
      pointer.x = -999;
      pointer.y = -999;
    };

    const styles = getComputedStyle(document.documentElement);
    const readColor = (name: string, fallback: string) =>
      styles.getPropertyValue(name).trim() || fallback;

    let raf = 0;
    let t = 0;

    const draw = () => {
      const accent = readColor("--clay", "#7c5cff");
      const signal = readColor("--signal", "#38e0c0");
      const muted = readColor("--ink-3", "#767c88");

      ctx.clearRect(0, 0, width, height);

      const mid = height / 2;
      const gap = 7;
      const bars = Math.floor(width / gap);
      // The "playhead" sweeps; everything behind it has been transcribed.
      const head = reduced ? bars * 0.62 : (t * 0.55) % (bars + 90) - 45;

      for (let i = 0; i < bars; i++) {
        const x = i * gap + gap / 2;
        const done = i < head;

        // Pointer bends nearby bars — the wave notices the cursor.
        const dx = pointer.x - x;
        const near = Math.max(0, 1 - Math.abs(dx) / 120);
        const lift = near * near * 26;

        if (done) {
          /* Resolved: two rows of word-shaped blocks, so the left half reads
             as subtitles rather than a flat line. Word breaks come from the
             same waveform, which keeps the rhythm tied to the audio. */
          const wordy = amplitudeAt(i * 0.7);
          const isGap = wordy > 0.32;
          if (!isGap) {
            const row = wordy < -0.2 ? 1 : 0;
            const y = mid + (row === 0 ? -7 : 6) - lift * 0.12;
            const h = 4.5;
            // Every so often a word carries the accent, like a caught keyword.
            const highlight = Math.abs(amplitudeAt(i * 0.13)) > 0.72;
            ctx.globalAlpha = highlight ? 0.85 : 0.42;
            ctx.fillStyle = highlight ? accent : muted;
            ctx.fillRect(x - gap / 2, y - h / 2, gap - 1.5, h);
          }
        } else {
          const wobble = reduced ? 0 : Math.sin(t * 0.05 + i * 0.3) * 3;
          const h = Math.abs(amplitudeAt(i)) * (height * 0.5) + 5 + wobble + lift;
          const fromHead = Math.max(0, Math.min(1, (i - head) / 26));
          ctx.globalAlpha = 0.25 + 0.75 * fromHead;
          ctx.fillStyle = i - head < 26 ? accent : signal;
          ctx.fillRect(x - 1.5, mid - h / 2, 3, h);
        }
      }

      // The playhead itself: a soft column of accent light.
      if (!reduced) {
        const hx = head * gap;
        const glow = ctx.createLinearGradient(hx - 40, 0, hx + 40, 0);
        glow.addColorStop(0, "transparent");
        glow.addColorStop(0.5, accent);
        glow.addColorStop(1, "transparent");
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = glow;
        ctx.fillRect(hx - 40, 0, 80, height);
      }

      ctx.globalAlpha = 1;
      t += 1;
      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerleave", onLeave);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  return <canvas ref={ref} className="hero-wave" aria-hidden="true" />;
}
