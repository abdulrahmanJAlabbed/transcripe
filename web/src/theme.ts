/** Light/dark, remembered.
 *
 * With no explicit choice the stylesheet follows the system through a media
 * query, so the page matches the OS on first paint. Choosing a side writes
 * data-theme, which wins over the query from then on.
 */
export type Theme = "light" | "dark";

const KEY = "transcripe.theme";
const PAPER = "#f7f7f9";
const CHARCOAL = "#121316";

function saved(): Theme | null {
  const value = localStorage.getItem(KEY);
  return value === "light" || value === "dark" ? value : null;
}

/* Dark is this app's default, so "no preference" means dark. */
function systemPrefersLight() {
  return window.matchMedia("(prefers-color-scheme: light)").matches;
}

/** What the page is showing right now, chosen or inherited. */
export function currentTheme(): Theme {
  return saved() ?? (systemPrefersLight() ? "light" : "dark");
}

/** Keep the browser chrome (address bar, PWA shell) in step with the page. */
function paintBrowserChrome(theme: Theme) {
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? CHARCOAL : PAPER);
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(KEY, theme);
  paintBrowserChrome(theme);
}

/** Follow the system until the reader states a preference. */
export function watchSystemTheme() {
  const query = window.matchMedia("(prefers-color-scheme: light)");
  const onChange = () => {
    if (!saved()) paintBrowserChrome(systemPrefersLight() ? "light" : "dark");
  };
  query.addEventListener("change", onChange);
  paintBrowserChrome(currentTheme());
  return () => query.removeEventListener("change", onChange);
}
