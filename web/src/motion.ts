import { useEffect, useRef } from "react";

/**
 * Reveal on scroll. Elements marked with data-reveal start hidden and rise in
 * as they enter the viewport, staggered by their position in a group so a grid
 * lands cell by cell rather than all at once.
 */
export function useScrollReveal() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      document
        .querySelectorAll<HTMLElement>("[data-reveal]")
        .forEach((el) => el.classList.add("is-revealed"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const el = entry.target as HTMLElement;
          const delay = Number(el.dataset.revealDelay ?? 0);
          window.setTimeout(() => el.classList.add("is-revealed"), delay);
          observer.unobserve(el);
        });
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.12 }
    );

    const watch = () =>
      document
        .querySelectorAll<HTMLElement>("[data-reveal]:not(.is-revealed)")
        .forEach((el) => observer.observe(el));

    watch();
    // Sections mount as state changes, so keep picking up new candidates.
    const rescan = window.setInterval(watch, 1200);
    return () => {
      window.clearInterval(rescan);
      observer.disconnect();
    };
  }, []);
}

/** A button that leans toward the cursor, then settles back. */
export function useMagnetic<T extends HTMLElement>(strength = 0.28) {
  const ref = useRef<T>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (window.matchMedia("(pointer: coarse)").matches) return;

    const onMove = (e: PointerEvent) => {
      const box = el.getBoundingClientRect();
      const dx = e.clientX - (box.left + box.width / 2);
      const dy = e.clientY - (box.top + box.height / 2);
      const reach = 90;
      const near =
        Math.abs(dx) < box.width / 2 + reach && Math.abs(dy) < box.height / 2 + reach;
      el.style.transform = near
        ? `translate(${dx * strength}px, ${dy * strength * 0.6}px)`
        : "";
    };
    const onLeave = () => {
      el.style.transform = "";
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerleave", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
    };
  }, [strength]);

  return ref;
}

/** Cards light up under the cursor: track the pointer as CSS variables. */
export function useSpotlight<T extends HTMLElement>() {
  const ref = useRef<T>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onMove = (e: PointerEvent) => {
      const box = el.getBoundingClientRect();
      el.style.setProperty("--sx", `${e.clientX - box.left}px`);
      el.style.setProperty("--sy", `${e.clientY - box.top}px`);
    };
    el.addEventListener("pointermove", onMove);
    return () => el.removeEventListener("pointermove", onMove);
  }, []);

  return ref;
}
