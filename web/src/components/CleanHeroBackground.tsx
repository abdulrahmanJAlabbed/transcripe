import React, { useEffect, useRef } from "react";

/**
 * Interactive dot-grid hero background.
 *
 * A faint grid of dots on a dark navy base; a soft spotlight follows the
 * cursor and brightens the dots inside its radius (plus an ambient glow).
 * Pure CSS rendering — two full-screen dotted layers where the bright layer
 * is masked to a radial gradient centred on --mx/--my. JS only updates those
 * two CSS variables (eased, via rAF), so it's GPU-cheap with no per-dot DOM.
 */
export const CleanHeroBackground: React.FC = () => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Target (tx,ty) = pointer; current (cx,cy) eases toward it for smooth motion.
    let tx = window.innerWidth / 2;
    let ty = window.innerHeight * 0.4;
    let cx = tx;
    let cy = ty;
    let raf = 0;

    const setTarget = (x: number, y: number) => {
      tx = x;
      ty = y;
    };
    const onMouse = (e: MouseEvent) => setTarget(e.clientX, e.clientY);
    const onTouch = (e: TouchEvent) => {
      if (e.touches[0]) setTarget(e.touches[0].clientX, e.touches[0].clientY);
    };

    const loop = () => {
      cx += (tx - cx) * 0.12;
      cy += (ty - cy) * 0.12;
      el.style.setProperty("--mx", `${cx.toFixed(1)}px`);
      el.style.setProperty("--my", `${cy.toFixed(1)}px`);
      raf = requestAnimationFrame(loop);
    };

    window.addEventListener("mousemove", onMouse);
    window.addEventListener("touchmove", onTouch, { passive: true });
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("mousemove", onMouse);
      window.removeEventListener("touchmove", onTouch);
    };
  }, []);

  return (
    <div ref={ref} className="hero-dotgrid" aria-hidden="true">
      <div className="hero-dotgrid__base" />
      <div className="hero-dotgrid__spot" />
      <div className="hero-dotgrid__glow" />
    </div>
  );
};
