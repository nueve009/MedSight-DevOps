import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { StatusTone } from "@/components/StatusBanner";
import { colors, radii, spacing } from "@/theme/theme";

type InfoCardProps = {
  tone: StatusTone;
  label: string;
  lines: string[];
};

export function InfoCard({ tone, label, lines }: InfoCardProps) {
  const toneColor = colors[tone];

  return (
    <View
      accessible
      accessibilityRole="summary"
      accessibilityLabel={`${label}. ${lines.join(" ")}`}
      style={[styles.card, { borderColor: toneColor }]}
    >
      <AppText
        variant="caption"
        bold
        color={toneColor}
        style={styles.label}
      >
        {label}
      </AppText>
      {lines.map((line) => (
        <AppText key={line} variant="body">
          {line}
        </AppText>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    width: "100%",
    gap: spacing.sm,
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderRadius: radii.lg,
  },
  label: {
    letterSpacing: 2,
    textTransform: "uppercase",
  },
});