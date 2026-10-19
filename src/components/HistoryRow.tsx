import { StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { Icon, IconName } from "@/components/Icon";
import { StatusTone } from "@/components/StatusBanner";
import { colors, radii, sizes, spacing } from "@/theme/theme";

type HistoryRowProps = {
  tone: StatusTone;
  title: string;
  statusLabel: string;
  time: string;
};

const BADGE_SIZE = 56;
const BADGE_ICON_SIZE = 36;

const toneIcon: Record<StatusTone, IconName> = {
  verified: "check",
  warning: "warning",
};

export function HistoryRow({
  tone,
  title,
  statusLabel,
  time,
}: HistoryRowProps) {
  return (
    <View
      accessible
      accessibilityRole="text"
      accessibilityLabel={`${title}. ${statusLabel}, ${time}`}
      style={styles.row}
    >
      <View style={[styles.badge, { backgroundColor: colors[tone] }]}>
        <Icon
          name={toneIcon[tone]}
          size={BADGE_ICON_SIZE}
          color={colors.onStatus}
        />
      </View>
      <View style={styles.text}>
        <AppText variant="title" bold>
          {title}
        </AppText>
        <AppText variant="bodySm" color={colors.textMuted}>
          {`${statusLabel} · ${time}`}
        </AppText>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    width: "100%",
    minHeight: sizes.listRow,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.border,
    borderRadius: radii.lg,
  },
  badge: {
    width: BADGE_SIZE,
    height: BADGE_SIZE,
    borderRadius: BADGE_SIZE / 2,
    alignItems: "center",
    justifyContent: "center",
  },
  text: {
    flex: 1,
    gap: spacing.xs / 2,
  },
});