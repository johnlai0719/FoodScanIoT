import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useFontScale } from '../contexts/FontScaleContext';
import { COMPETITION_DEMO_CASES, COMPETITION_DEMO_MODES, CompetitionDemoMode } from '../competition/demoCases';
import { reportColors as C } from './reportUi';

export default function CompetitionDemoPanel({ onPreview, mode, onMode }: {
  onPreview: (id: string) => void; mode: CompetitionDemoMode | null; onMode: (id: CompetitionDemoMode) => void;
}) {
  const { fontScale } = useFontScale();
  return <View style={s.panel}>
    <Text style={[s.heading, { fontSize: 14 * fontScale }]}>介面預覽</Text>
    <Text style={[s.note, { fontSize: 12 * fontScale }]}>虛構案例，不呼叫雲端、不寫入查詢歷史，也不觸發儀表板完成事件。</Text>
    {COMPETITION_DEMO_CASES.map(item => <Pressable key={item.id} accessibilityRole="button" onPress={() => onPreview(item.id)} style={s.option}>
      <Text style={[s.title, { fontSize: 13 * fontScale }]}>{item.title}</Text><Text style={[s.note, { fontSize: 12 * fontScale }]}>{item.description}</Text>
    </Pressable>)}
    <Text style={[s.heading, { fontSize: 14 * fontScale, marginTop: 8 }]}>實際連線展示</Text>
    <Text style={[s.note, { fontSize: 12 * fontScale }]}>選擇只會切換分析設定。照片與結果皆走實際 Fog 流程，請回到上方拍照或輸入條碼後送出。</Text>
    {COMPETITION_DEMO_MODES.map(item => <Pressable key={item.id} accessibilityRole="radio" accessibilityState={{ checked: mode === item.id }} aria-checked={mode === item.id} onPress={() => onMode(item.id)} style={[s.option, mode === item.id && s.selected]}>
      <Text style={[s.title, { fontSize: 13 * fontScale }]}>{mode === item.id ? '已選擇 · ' : ''}{item.title}</Text><Text style={[s.note, { fontSize: 12 * fontScale }]}>{item.description}</Text>
    </Pressable>)}
  </View>;
}
const s = StyleSheet.create({
  panel: { gap: 10, padding: 13, borderRadius: 13, backgroundColor: '#EEF6F1' },
  heading: { color: C.text, fontWeight: '800' }, title: { color: C.green, fontWeight: '700' },
  note: { color: C.muted, lineHeight: 21 }, option: { gap: 5, padding: 12, borderWidth: 1, borderColor: '#D6E6DC', borderRadius: 10, backgroundColor: '#FFFFFF' },
  selected: { borderColor: C.green, backgroundColor: '#E4F5EB' },
});
