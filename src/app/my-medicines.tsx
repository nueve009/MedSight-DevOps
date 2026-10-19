import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, FlatList, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { CornerButton } from "@/components/CornerButton";
import { MedicineRow } from "@/components/MedicineRow";
import { PrimaryButton } from "@/components/PrimaryButton";
import { Screen } from "@/components/Screen";
import { TextField } from "@/components/TextField";
import { messages } from "@/constants/messages";
import { useSpeech } from "@/hooks/useSpeech";
import {
  addMyMedicine,
  deleteMyMedicine,
  getMyMedicines,
} from "@/services/db";
import { colors, spacing } from "@/theme/theme";
import { SavedMedicine } from "@/types";

// Caregiver screen: add and remove the medicines this person takes.
export default function MyMedicinesScreen() {
  // null = still loading, so the empty message doesn't flash first.
  const [medicines, setMedicines] = useState<SavedMedicine[] | null>(null);
  const [name, setName] = useState("");
  const [doseNote, setDoseNote] = useState("");
  // Shown (and spoken) under the form when adding didn't work.
  const [problem, setProblem] = useState("");
  const { speak } = useSpeech();

  const reload = useCallback(async () => {
    try {
      setMedicines(await getMyMedicines());
    } catch {
      setMedicines([]);
    }
  }, []);

  // Load the saved list every time this screen comes to the front.
  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );

  function showProblem(message: string) {
    setProblem(message);
    speak(message);
  }

  async function handleAdd() {
    const cleanName = name.trim();
    if (!cleanName) {
      showProblem(messages.myMedicines.nameMissing);
      return;
    }

    try {
      const added = await addMyMedicine(cleanName, doseNote.trim());
      if (!added) {
        showProblem(messages.myMedicines.alreadySaved(cleanName));
        return;
      }
    } catch {
      showProblem(messages.myMedicines.saveFailed);
      return;
    }

    setName("");
    setDoseNote("");
    setProblem("");
    speak(messages.myMedicines.added(cleanName));
    await reload();
  }

  // Removing can't be undone, so ask first.
  function handleRemove(medicine: SavedMedicine) {
    Alert.alert(
      messages.myMedicines.removeConfirmTitle(medicine.name),
      messages.myMedicines.removeConfirmBody,
      [
        { text: messages.myMedicines.removeCancel, style: "cancel" },
        {
          text: messages.myMedicines.remove,
          style: "destructive",
          onPress: async () => {
            try {
              await deleteMyMedicine(medicine.id);
              speak(messages.myMedicines.removed(medicine.name));
            } catch {
              showProblem(messages.myMedicines.saveFailed);
            }
            await reload();
          },
        },
      ],
    );
  }

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
          {messages.myMedicines.title}
        </AppText>
      </View>

      <FlatList
        data={medicines ?? []}
        keyExtractor={(item) => String(item.id)}
        accessibilityLabel={messages.myMedicines.listLabel}
        style={styles.list}
        contentContainerStyle={styles.listContent}
        // Lets a tap on "Add" or "Remove" work while the keyboard is open.
        keyboardShouldPersistTaps="handled"
        // The add form sits above the list so the keyboard never covers it.
        ListHeaderComponent={
          <View style={styles.form}>
            <TextField
              label={messages.myMedicines.nameLabel}
              helper={messages.myMedicines.nameHelper}
              value={name}
              onChangeText={setName}
            />
            <TextField
              label={messages.myMedicines.doseNoteLabel}
              helper={messages.myMedicines.doseNoteHelper}
              value={doseNote}
              onChangeText={setDoseNote}
              onSubmitEditing={handleAdd}
            />
            {problem ? (
              <AppText
                variant="bodySm"
                bold
                color={colors.warning}
                accessibilityLiveRegion="polite"
              >
                {problem}
              </AppText>
            ) : null}
            <PrimaryButton
              label={messages.myMedicines.add}
              onPress={handleAdd}
              accessibilityHint={messages.myMedicines.addHint}
            />

            <AppText
              variant="bodySm"
              bold
              color={colors.textMuted}
              accessibilityRole="header"
              style={styles.listHeading}
            >
              {messages.myMedicines.listLabel}
            </AppText>
          </View>
        }
        renderItem={({ item }) => (
          <MedicineRow
            name={item.name}
            doseNote={item.doseNote}
            removeLabel={messages.myMedicines.remove}
            removeAccessibilityLabel={messages.myMedicines.removeLabel(
              item.name,
            )}
            removeAccessibilityHint={messages.myMedicines.removeHint}
            onRemove={() => handleRemove(item)}
          />
        )}
        ListEmptyComponent={
          medicines === null ? null : (
            <View accessible style={styles.empty}>
              <AppText variant="title" bold>
                {messages.myMedicines.empty}
              </AppText>
              <AppText variant="body" color={colors.textMuted}>
                {messages.myMedicines.emptyHint}
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
  // "My medicines" at 40pt is wide next to the Back button, so it must be
  // allowed to wrap on narrow phones.
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
  form: {
    gap: spacing.md,
    paddingBottom: spacing.xs,
  },
  listHeading: {
    paddingTop: spacing.md,
  },
  empty: {
    gap: spacing.xs,
  },
});
