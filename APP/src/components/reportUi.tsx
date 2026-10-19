import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { ArrowLeft, ChevronRight } from 'lucide-react-native';
import { useFontScale } from '../contexts/FontScaleContext';

export const reportColors = {
  text: '#151C20', muted: '#737B80', green: '#009B58', mint: '#E9F7F0',
  border: '#E2E5E5', teal: '#0097A7', background: '#F6F7F6', red: '#DB2429',
};

export function ScreenHeader({ title, onBack, right }: {
  title: string; onBack: () => void; right?: React.ReactNode;
}) {
  const { fontScale } = useFontScale();
  return <View style={ui.header}>
    <Pressable onPress={onBack} accessibilityRole="button" accessibilityLabel="返回" style={ui.back}>
      <ArrowLeft size={19} strokeWidth={1.9} color={reportColors.green} />
      <Text style={[ui.backText, { fontSize: 13 * fontScale }]}>返回</Text>
    </Pressable>
    <Text accessibilityRole="header" style={[ui.headerTitle, { fontSize: 19 * fontScale }]}>{title}</Text>
    <View style={ui.headerRight}>{right}</View>
  </View>;
}

export function DetailButton({ title, onPress }: { title: string; onPress: () => void }) {
  const { fontScale } = useFontScale();
  return <Pressable onPress={onPress} accessibilityRole="button"
    style={({ pressed }) => [ui.detailButton, pressed && { opacity: 0.65 }]}>
    <Text style={[ui.detailButtonText, { fontSize: 14 * fontScale }]}>{title}</Text>
    <ChevronRight size={19} color={reportColors.green} strokeWidth={2} />
  </Pressable>;
}

export const ui = StyleSheet.create({
  stack: { gap: 12 },
  card: { backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: reportColors.border, borderRadius: 16, padding: 15 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 10, minHeight: 48, marginBottom: 4 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 3, minHeight: 40, paddingRight: 8 },
  backText: { color: reportColors.green, fontWeight: '600' },
  headerTitle: { flex: 1, textAlign: 'center', color: reportColors.text, fontWeight: '800' },
  headerRight: { minWidth: 53, alignItems: 'flex-end' },
  detailButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7,
    minHeight: 42, marginTop: 13, backgroundColor: '#E9F8F1', borderWidth: 1, borderColor: '#CBEDDE', borderRadius: 11, padding: 10 },
  detailButtonText: { color: reportColors.green, fontWeight: '800', textAlign: 'center' },
  note: { color: reportColors.muted, fontSize: 13, lineHeight: 21 },
  sectionTitle: { color: reportColors.text, fontSize: 18, fontWeight: '800', flex: 1 },
  iconButton: { minHeight: 40, minWidth: 40, alignItems: 'center', justifyContent: 'center', borderRadius: 12 },
});
