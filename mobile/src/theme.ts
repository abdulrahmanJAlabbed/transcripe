/** Same quiet studio palette as the web app — warm paper, ink, one clay accent. */
export const c = {
  paper: "#faf9f6",
  card: "#fffefb",
  well: "#f2f0ea",
  ink: "#1d1c18",
  ink2: "#55524b",
  ink3: "#8b877c",
  line: "#e8e5dd",
  lineStrong: "#d8d4c9",
  clay: "#c8502a",
  clayDeep: "#ad431f",
  claySoft: "rgba(200, 80, 42, 0.08)",
  ok: "#2c6e49",
  okSoft: "rgba(44, 110, 73, 0.1)",
  err: "#b3372a",
  errSoft: "rgba(179, 55, 42, 0.08)"
} as const;

export const f = {
  display: "Fraunces_500Medium",
  displayItalic: "Fraunces_500Medium_Italic",
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

export const shadow = {
  card: {
    shadowColor: "#1d1c18",
    shadowOpacity: 0.08,
    shadowRadius: 22,
    shadowOffset: { width: 0, height: 10 },
    elevation: 3
  },
  cta: {
    shadowColor: c.clay,
    shadowOpacity: 0.35,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 },
    elevation: 4
  }
} as const;
