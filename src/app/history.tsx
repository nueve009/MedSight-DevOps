import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { FlatList, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { CornerButton } from "@/components/CornerButton";
import { HistoryRow } from "@/components/HistoryRow";
import { Screen } from "@/components/Screen";
import { StatusTone } from "@/components/StatusBanner";
import { messages } from "@/constants/messages";
import { getHistory } from "@/services/db";
import { colors, spacing } from "@/theme/theme";
import { HistoryItem } from "@/types";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

// Turns a saved scan into the words and color its row shows.
function describe(item: HistoryItem): {
  tone: StatusTone;
  title: string;
  statusLabel: string;
} {
  if (item.status === "unreadable") {
    return {
      tone: "warning",
      title: messages.history.couldNotRead,
      statusLabel: messages.history.status.tryAgain,
    };
  }

  // "Biogesic 500 mg", or just the name when the strength wasn't read.
  const title = item.name
    ? [item.name, item.strength].filter(Boolean).join(" ")
    : messages.history.unknownMedicine;

  return item.status === "verified"
    ? {
        tone: "verified",
        title,
        statusLabel: messages.history.status.verified,
      }
    : {
        tone: "warning",
        title,
        statusLabel: messages.history.status.notVerified,
      };
}

// "Today, 8:14 AM" · "Yesterday, 9:40 PM" · "Mon, 6:30 PM" · "Oct 3, 6:30 PM"
function formatScanTime(isoDate: string): string {
  const date = new Date(isoDate);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfScanDay = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
  );
  const daysAgo = Math.round(
    (startOfToday.getTime() - startOfScanDay.getTime()) / MS_PER_DAY,
  );

  let day: string;
  if (daysAgo <= 0) {
    day = messages.history.today;
  } else if (daysAgo === 1) {
    day = messages.history.yesterday;
  } else if (daysAgo < 7) {
    day = date.toLocaleDateString(undefined, { weekday: "short" });
  } else {
    day = date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  }

  const time = date.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  return messages.history.dateTime(day, time);
}

export default function HistoryScreen() {
  // null = still loading, so "No scans yet" doesn't flash before the list.
  const [items, setItems] = useState<HistoryItem[] | null>(null);

  // Load the saved scans every time this screen comes to the front.
  useFocusEffect(
    useCallback(() => {
      let active = true;
      getHistory()
        .then((saved) => {
          if (active) {
            setItems(saved);
          }
        })
        .catch(() => {
          if (active) {
            setItems([]);
          }
        });
      return () => {
        active = false;
      };
    }, []),
  );

  return (
    <Screen>
      <View style={styles.header}>
        <CornerButton
          label={messages.common.back}
          icon="back"
          onPress={() => router.back()}
          accessibilityHint={messages.common.backHint}
        />
        <AppText
          variant="h1"
          bold
          accessibilityRole="header"
          style={styles.title}
        >
          {messages.history.title}
        </AppText>
      </View>

      <FlatList
        data={items ?? []}
        keyExtractor={(item) => String(item.id)}
        accessibilityLabel={messages.history.listLabel}
        style={styles.list}
        contentContainerStyle={styles.listContent}
        renderItem={({ item }) => {
          const row = describe(item);
          return (
            <HistoryRow
              tone={row.tone}
              title={row.title}
              statusLabel={row.statusLabel}
              time={formatScanTime(item.scannedAt)}
            />
          );
        }}
        ListEmptyComponent={
          items === null ? null : (
            <View accessible style={styles.empty}>
              <AppText variant="title" bold>
                {messages.history.empty}
              </AppText>
              <AppText variant="body" color={colors.textMuted}>
                {messages.history.emptyHint}
              </AppText>
            </View>
          )
        }
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingBottom: spacing.md,
  },
  // Lets a long title wrap instead of running off the screen.
  title: {
    flexShrink: 1,
  },
  list: {
    flex: 1,
  },
  listContent: {
    flexGrow: 1,
    gap: spacing.sm,
    paddingBottom: spacing.lg,
  },
  empty: {
    gap: spacing.xs,
    paddingTop: spacing.lg,
  },
});
