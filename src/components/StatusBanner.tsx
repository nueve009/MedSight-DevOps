import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon, IconName } from "@/components/Icon";
import { colors, spacing } from "@/theme/theme";

export type StatusTone = "verified" | "warning";

type StatusBannerProps = {
  tone: StatusTone;
  label: string;
};

const BADGE_SIZE = 56;
const BADGE_ICON_SIZE = 36;

const toneIcon: Record<StatusTone, IconName> = {
  verified: "check",
  warning: "warning",
};

export function StatusBanner({ tone, label }: StatusBannerProps) {
  const toneColor = colors[tone];

  return (
    <View
      accessible
      accessibilityRole="header"
      accessibilityLabel={label}
      style={[styles.banner, { backgroundColor: toneColor }]}
    >
      <View style={styles.badge}>
        <Icon name={toneIcon[tone]} size={BADGE_ICON_SIZE} color={toneColor} />
      </View>
      <AppText
        variant="title"
        bold
        color={colors.onStatus}
        style={styles.label}
      >
        {label}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    width: "100%",
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.lg,
  },
  badge: {
    width: BADGE_SIZE,
    height: BADGE_SIZE,
    borderRadius: BADGE_SIZE / 2,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.onStatus,
  },
  label: {
    flexShrink: 1,
    letterSpacing: 2,
    textTransform: "uppercase",
  },
});