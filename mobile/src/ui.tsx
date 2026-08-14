import * as Haptics from "expo-haptics";
import { ReactNode, useEffect, useMemo, useRef } from "react";
import {
  ActivityIndicator,
  Animated,
  Easing,
  Pressable,
  StyleProp,
  StyleSheet,
  Text,
  TextStyle,
  View,
  ViewStyle
} from "react-native";
import { f, Palette, radius, shadows, usePalette } from "./theme";

/* Styles depend on the palette, so they're built per theme and memoised
   rather than created once at module load. */
const makeStyles = (c: Palette, isDark: boolean) => {
  const shadow = shadows(isDark);
  return StyleSheet.create({
    display: {
      fontFamily: f.display,
      fontSize: 33,
      lineHeight: 38,
      color: c.ink,
      letterSpacing: -1.2
    },
    label: {
      fontFamily: f.mono,
      fontSize: 10.5,
      letterSpacing: 1.6,
      color: c.ink3,
      textTransform: "uppercase"
    },
    body: { fontFamily: f.body, fontSize: 15, lineHeight: 22, color: c.ink2 },
    mono: { fontFamily: f.mono, fontSize: 12, color: c.ink3 },

    chip: {
      paddingVertical: 9,
      paddingHorizontal: 15,
      borderRadius: radius.chip,
      borderWidth: 1,
      borderColor: c.lineStrong,
      backgroundColor: c.card
    },
    chipActive: { backgroundColor: c.clay, borderColor: c.clay },
    chipText: { fontFamily: f.mono, fontSize: 12.5, color: c.ink2 },
    chipTextActive: { color: c.onAccent },

    cta: {
      height: 54,
      borderRadius: radius.field,
      backgroundColor: c.clay,
      justifyContent: "center",
      alignItems: "center",
      ...shadow.cta
    },
    ctaOff: { backgroundColor: c.well, shadowOpacity: 0, elevation: 0 },
    ctaInner: { flexDirection: "row", alignItems: "center" },
    ctaText: { fontFamily: f.bodySemi, fontSize: 16, color: c.onAccent },
    ctaTextOff: { color: c.ink3 },

    seg: {
      flexDirection: "row",
      backgroundColor: c.well,
      borderRadius: radius.inner,
      padding: 3
    },
    segThumb: {
      position: "absolute",
      top: 3,
      bottom: 3,
      left: 3,
      backgroundColor: c.card,
      borderRadius: 9,
      shadowColor: "#000",
      shadowOpacity: isDark ? 0.4 : 0.1,
      shadowRadius: 3,
      shadowOffset: { width: 0, height: 1 },
      elevation: 1
    },
    segText: { fontFamily: f.bodyMedium, fontSize: 14, color: c.ink3 },
    segTextActive: { color: c.ink },

    track: {
      height: 4,
      borderRadius: 2,
      backgroundColor: c.well,
      overflow: "hidden"
    },
    trackSlide: {
      height: 4,
      width: 90,
      borderRadius: 2,
      backgroundColor: c.clay
    }
  });
};

function useStyles() {
  const { c, isDark } = usePalette();
  return useMemo(() => ({ s: makeStyles(c, isDark), c }), [c, isDark]);
}

/* ── Type ────────────────────────────────────────────────────────────────── */

export function Display({ children, style }: { children: ReactNode; style?: TextStyle }) {
  const { s } = useStyles();
  return <Text style={[s.display, style]}>{children}</Text>;
}

export function Label({ children }: { children: ReactNode }) {
  const { s } = useStyles();
  return <Text style={s.label}>{children}</Text>;
}

export function Body({ children, style }: { children: ReactNode; style?: TextStyle }) {
  const { s } = useStyles();
  return <Text style={[s.body, style]}>{children}</Text>;
}

export function Mono({ children, style }: { children: ReactNode; style?: TextStyle }) {
  const { s } = useStyles();
  return <Text style={[s.mono, style]}>{children}</Text>;
}

/* ── Press feedback: everything dips slightly and taps back ──────────────── */

export function Tap({
  children,
  onPress,
  style,
  disabled,
  haptic = "light"
}: {
  children: ReactNode;
  onPress: () => void;
  style?: StyleProp<ViewStyle>;
  disabled?: boolean;
  haptic?: "light" | "medium" | "none";
}) {
  return (
    <Pressable
      disabled={disabled}
      onPress={() => {
        if (haptic !== "none") {
          Haptics.impactAsync(
            haptic === "medium"
              ? Haptics.ImpactFeedbackStyle.Medium
              : Haptics.ImpactFeedbackStyle.Light
          ).catch(() => {});
        }
        onPress();
      }}
      style={({ pressed }) => [
        style,
        pressed && !disabled ? { opacity: 0.82, transform: [{ scale: 0.985 }] } : null
      ]}
    >
      {children}
    </Pressable>
  );
}

/* ── Chip ────────────────────────────────────────────────────────────────── */

export function Chip({
  label,
  active,
  onPress
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  const { s } = useStyles();
  return (
    <Tap onPress={onPress} style={[s.chip, active && s.chipActive]}>
      <Text style={[s.chipText, active && s.chipTextActive]}>{label}</Text>
    </Tap>
  );
}

/* ── Primary action ──────────────────────────────────────────────────────── */

export function Cta({
  label,
  onPress,
  disabled,
  busy
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  const { s, c } = useStyles();
  return (
    <Tap
      onPress={onPress}
      disabled={disabled || busy}
      haptic="medium"
      style={[s.cta, (disabled || busy) && s.ctaOff]}
    >
      <View style={s.ctaInner}>
        {busy && <ActivityIndicator color={c.onAccent} style={{ marginRight: 9 }} />}
        <Text style={[s.ctaText, (disabled || busy) && s.ctaTextOff]}>{label}</Text>
      </View>
    </Tap>
  );
}

/* ── Segmented control with a sliding thumb ──────────────────────────────── */

export function Segmented({
  options,
  value,
  onChange,
  width
}: {
  options: Array<{ key: string; label: string }>;
  value: string;
  onChange: (key: string) => void;
  width: number;
}) {
  const { s } = useStyles();
  const idx = Math.max(0, options.findIndex((o) => o.key === value));
  const slot = (width - 6) / options.length;
  const x = useRef(new Animated.Value(idx * slot)).current;

  useEffect(() => {
    Animated.timing(x, {
      toValue: idx * slot,
      duration: 260,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true
    }).start();
  }, [idx, slot, x]);

  return (
    <View style={[s.seg, { width }]}>
      <Animated.View
        style={[s.segThumb, { width: slot, transform: [{ translateX: x }] }]}
      />
      {options.map((o) => (
        <Tap
          key={o.key}
          onPress={() => onChange(o.key)}
          style={{ width: slot, paddingVertical: 11, alignItems: "center" }}
        >
          <Text style={[s.segText, value === o.key && s.segTextActive]}>
            {o.label}
          </Text>
        </Tap>
      ))}
    </View>
  );
}

/* ── Indeterminate progress bar ──────────────────────────────────────────── */

export function Track({ animate }: { animate: Animated.Value }) {
  const { s } = useStyles();
  return (
    <View style={s.track}>
      <Animated.View
        style={[
          s.trackSlide,
          {
            transform: [
              {
                translateX: animate.interpolate({
                  inputRange: [0, 1],
                  outputRange: [-90, 320]
                })
              }
            ]
          }
        ]}
      />
    </View>
  );
}

export function loopSlide(v: Animated.Value) {
  v.setValue(0);
  return Animated.loop(
    Animated.timing(v, {
      toValue: 1,
      duration: 1300,
      easing: Easing.inOut(Easing.quad),
      useNativeDriver: true
    })
  );
}
