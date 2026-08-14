import { useColorScheme } from "react-native";

/** Same palette as the web studio: dark by default, one violet accent. */
export type Palette = {
  paper: string;
  card: string;
  well: string;
  ink: string;
  ink2: string;
  ink3: string;
  line: string;
  lineStrong: string;
  clay: string;
  clayDeep: string;
  claySoft: string;
  ok: string;
  okSoft: string;
  err: string;
  errSoft: string;
  onAccent: string;
};

export const dark: Palette = {
  paper: "#121316",
  card: "#191b1f",
  well: "#212429",
  ink: "#ecedf0",
  ink2: "#a8adb8",
  ink3: "#767c88",
  line: "#24272d",
  lineStrong: "#333842",
  clay: "#7c5cff",
  clayDeep: "#9179ff",
  claySoft: "rgba(124, 92, 255, 0.14)",
  ok: "#3ecf8e",
  okSoft: "rgba(62, 207, 142, 0.14)",
  err: "#ff7a6e",
  errSoft: "rgba(255, 122, 110, 0.12)",
  onAccent: "#ffffff"
};

export const light: Palette = {
  paper: "#f7f7f9",
  card: "#ffffff",
  well: "#eef0f3",
  ink: "#14151a",
  ink2: "#4b505c",
  ink3: "#7c828f",
  line: "#e6e7ec",
  lineStrong: "#d3d6de",
  clay: "#6d4bf5",
  clayDeep: "#5a38e6",
  claySoft: "rgba(109, 75, 245, 0.08)",
  ok: "#12855c",
  okSoft: "rgba(18, 133, 92, 0.12)",
  err: "#d8443a",
  errSoft: "rgba(216, 68, 58, 0.08)",
  onAccent: "#ffffff"
};

/** Dark is this app's default, so anything but an explicit "light" stays dark. */
export function usePalette(): { c: Palette; isDark: boolean } {
  const scheme = useColorScheme();
  const isDark = scheme !== "light";
  return { c: isDark ? dark : light, isDark };
}

export const f = {
  display: "Inter_700Bold",
  body: "Inter_400Regular",
  bodyMedium: "Inter_500Medium",
  bodySemi: "Inter_600SemiBold",
  mono: "JetBrainsMono_400Regular",
  monoMedium: "JetBrainsMono_500Medium"
} as const;

export const radius = {
  card: 22,
  field: 14,
  chip: 999,
  inner: 12
} as const;

export function shadows(isDark: boolean) {
  return {
    card: {
      shadowColor: "#000",
      shadowOpacity: isDark ? 0.5 : 0.08,
      shadowRadius: 22,
      shadowOffset: { width: 0, height: 10 },
      elevation: 3
    },
    cta: {
      shadowColor: isDark ? "#000" : "#6d4bf5",
      shadowOpacity: isDark ? 0.55 : 0.3,
      shadowRadius: 14,
      shadowOffset: { width: 0, height: 6 },
      elevation: 4
    }
  } as const;
}
