import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { ChevronDown, ChevronRight, Package, ScanLine, Settings2, ScanFace } from 'lucide-react-native';
import { HistoryEntry, relativeTime } from '../utils/scanHistory';
import { useFontScale } from '../contexts/FontScaleContext';
import { reportColors as C } from './reportUi';

export default function ScanDashboard({ barcode, onBarcodeChange, onBarcodeScan, imagePicker,
  canAnalyze, onAnalyze, history, onHistory, onClearHistory, onDemo, onSettings, endpointLabel }: {
  barcode: string; onBarcodeChange: (value: string) => void; onBarcodeScan: () => void;
  imagePicker: React.ReactNode; canAnalyze: boolean; onAnalyze: () => void;
  history: HistoryEntry[]; onHistory: (entry: HistoryEntry) => void; onClearHistory: () => void;
  onDemo: (code: string) => void; onSettings: () => void; endpointLabel: string;
}) {
  const { fontScale } = useFontScale();
  const s = useMemo(() => styles(fontScale), [fontScale]);
  const [showDemo, setShowDemo] = useState(false);
  return <View style={s.page}>
    <View style={s.intro}><Text accessibilityRole="header" style={s.title}>掃描食品</Text><Text style={s.subtitle}>掃條碼或拍攝標示照片，開始分析食品成分與營養。</Text></View>
    <View style={s.barcodeRow}>
      <TextInput value={barcode} onChangeText={onBarcodeChange} keyboardType="numeric" placeholder="輸入條碼，例如 4710018123456" placeholderTextColor="#8D9AAF" style={s.input} accessibilityLabel="食品條碼" />
      <Pressable onPress={onBarcodeScan} accessibilityRole="button" accessibilityLabel="開啟條碼掃描器" style={s.scan}><ScanLine size={24} color={C.green} strokeWidth={1.7} /></Pressable>
    </View>
    {imagePicker}
    <Pressable onPress={onAnalyze} disabled={!canAnalyze} accessibilityRole="button" accessibilityState={{ disabled: !canAnalyze }} style={({ pressed }) => [s.analyze, !canAnalyze && { backgroundColor: '#5AAEB5' }, pressed && { opacity: 0.8 }]}><Text style={s.analyzeText}>開始分析</Text></Pressable>
    <View style={s.history}>
      <View style={s.historyHeader}><Text style={s.historyTitle}>最近查過</Text>{history.length > 0 && <Pressable onPress={onClearHistory} accessibilityRole="button" accessibilityLabel="清除查詢紀錄" hitSlop={10}><Text style={s.clear}>清除</Text></Pressable>}</View>
      {history.length === 0 ? <Text style={s.emptyHistory}>分析過的產品會顯示在這裡。</Text> : history.map(entry => <Pressable key={entry.id} onPress={() => onHistory(entry)} accessibilityRole="button" style={({ pressed }) => [s.historyRow, pressed && { opacity: 0.65 }]}>
        <View style={s.historyImage}><Package size={22} color="#7F9C8B" strokeWidth={1.5} /></View>
        <View style={{ flex: 1, gap: 4 }}><Text style={s.historyName} numberOfLines={2}>{entry.name}</Text><Text style={s.historyTime}>{relativeTime(entry.savedAt)}</Text></View><ChevronRight size={19} color="#8A9296" />
      </Pressable>)}
    </View>
    <View style={s.secondary}>
      <Pressable accessibilityRole="button" aria-expanded={showDemo} accessibilityState={{ expanded: showDemo }} onPress={() => setShowDemo(!showDemo)} style={s.secondaryRow}><ScanFace size={21} color="#778287" strokeWidth={1.6} /><Text style={s.secondaryText}>Demo 範例資料</Text><View style={s.testBadge}><Text style={s.testBadgeText}>TEST</Text></View>{showDemo ? <ChevronDown size={17} color={C.muted} /> : <ChevronRight size={17} color={C.muted} />}</Pressable>
      {showDemo && <View style={s.demoOptions}><Text style={s.demoNote}>以下為示範資料，供介面預覽使用。</Text>{['4710018123456', 'TEST'].map(code => <Pressable key={code} accessibilityRole="button" onPress={() => onDemo(code)} style={s.demoOption}><Text style={s.demoOptionText}>載入範例 {code}</Text><ChevronRight size={16} color={C.green} /></Pressable>)}</View>}
      <Pressable accessibilityRole="button" onPress={onSettings} style={s.secondaryRow}><Settings2 size={21} color="#778287" strokeWidth={1.6} /><Text style={s.secondaryText}>進階設定</Text><View style={s.endpointBadge}><Text style={s.endpointText}>{endpointLabel}</Text></View><ChevronRight size={17} color={C.muted} /></Pressable>
    </View>
  </View>;
}

const styles = (scale: number) => StyleSheet.create({
  page: { gap: 14 }, intro: { marginTop: 30, marginBottom: 8, gap: 7 }, title: { fontSize: 25 * scale, fontWeight: '800', color: C.text }, subtitle: { fontSize: 13 * scale, color: C.muted, lineHeight: 21 * scale },
  barcodeRow: { flexDirection: 'row', gap: 9, alignItems: 'center' }, input: { flex: 1, minWidth: 0, minHeight: 51, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: C.border, borderRadius: 13, paddingHorizontal: 13, fontSize: 13 * scale, color: C.text }, scan: { width: 51, height: 51, borderRadius: 13, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: C.border, alignItems: 'center', justifyContent: 'center' },
  analyze: { minHeight: 49, backgroundColor: C.teal, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginTop: 2 }, analyzeText: { fontSize: 16 * scale, fontWeight: '800', color: '#FFFFFF' },
  history: { marginTop: 17, gap: 8 }, historyHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, historyTitle: { color: C.text, fontSize: 15 * scale, fontWeight: '800' }, clear: { color: C.muted, fontSize: 13 * scale }, emptyHistory: { color: '#9AA1A4', fontSize: 13 * scale, paddingVertical: 14 }, historyRow: { flexDirection: 'row', alignItems: 'center', gap: 12, borderBottomWidth: 1, borderBottomColor: '#E5E8E8', paddingVertical: 12 }, historyImage: { width: 48, height: 52, borderRadius: 9, backgroundColor: '#E7F0E9', alignItems: 'center', justifyContent: 'center' }, historyName: { fontSize: 14 * scale, fontWeight: '700', lineHeight: 21 * scale, color: C.text }, historyTime: { color: C.muted, fontSize: 12 * scale },
  secondary: { marginTop: 10, gap: 9 }, secondaryRow: { flexDirection: 'row', gap: 10, alignItems: 'center', minHeight: 51, paddingHorizontal: 13, paddingVertical: 11, backgroundColor: '#FFFFFF', borderRadius: 13, borderWidth: 1, borderColor: C.border }, secondaryText: { flex: 1, color: '#626C71', fontSize: 14 * scale, fontWeight: '600' }, testBadge: { backgroundColor: '#E8F8F0', borderRadius: 5, borderWidth: 1, borderColor: '#C8EEDC', paddingHorizontal: 7, paddingVertical: 3 }, testBadgeText: { color: C.green, fontSize: 10 * scale, fontWeight: '600' }, endpointBadge: { backgroundColor: '#E6F6FA', borderWidth: 1, borderColor: '#C6E7EE', borderRadius: 6, paddingHorizontal: 7, paddingVertical: 4 }, endpointText: { color: '#008CA1', fontSize: 10 * scale, fontWeight: '700' },
  demoOptions: { borderRadius: 12, padding: 13, backgroundColor: '#EEF6F1', gap: 8 }, demoNote: { fontSize: 12 * scale, color: C.muted }, demoOption: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 8 }, demoOptionText: { color: C.green, fontSize: 13 * scale, fontWeight: '600' },
});
