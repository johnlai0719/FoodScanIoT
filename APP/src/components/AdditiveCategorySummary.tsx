import { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { FlaskConical } from 'lucide-react-native';
import type { IngredientDetail } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { getAdditiveCategoryStats } from '../utils/resultPresentation';
import { DetailButton, reportColors as C, ui } from './reportUi';

const COLORS = ['#008F79', '#59ADA2', '#D6A143', '#9795A7', '#658BAF', '#9B789A', '#7B9B53', '#BB8769'];

export default function AdditiveCategorySummary({ ingredients, onDetails }: {
  ingredients: IngredientDetail[];
  onDetails: () => void;
}) {
  const { fontScale } = useFontScale();
  const s = useMemo(() => styles(fontScale), [fontScale]);
  const { additiveCount, rows, totalAssignments } = getAdditiveCategoryStats(ingredients);
  const hasMultipleFunctions = totalAssignments > additiveCount;

  return <View style={ui.card}>
    <View style={s.header}>
      <View style={s.heading}>
        <View style={ui.row}>
          <FlaskConical size={22} color={C.green} strokeWidth={1.8} />
          <Text accessibilityRole="header" style={s.title}>添加物分析</Text>
        </View>
        <Text style={s.subtitle}>本次比對到的食品添加物</Text>
      </View>
      <View style={s.total} accessible accessibilityLabel={`共 ${additiveCount} 項添加物`}>
        <Text style={s.number}>{additiveCount}</Text>
        <Text style={s.totalLabel}>項添加物</Text>
      </View>
    </View>

    {rows.length > 0 ? <>
      <Text style={s.sectionLabel}>依功能分類</Text>
      <View style={s.bar} accessible accessibilityLabel={`功能分類${hasMultipleFunctions ? '項次' : '數量'}占比；${rows.map(row => `${row.name} ${row.count} 項`).join('，')}`}>
        {rows.map((row, index) => <View key={row.name} style={{
          flexGrow: row.count, flexBasis: 0, height: '100%',
          backgroundColor: row.name === '未分類' ? '#A1A8AC' : COLORS[index % COLORS.length],
          borderLeftWidth: index ? 2 : 0, borderLeftColor: '#FFFFFF',
        }} />)}
      </View>
      <View style={s.legend}>
        {rows.map((row, index) => <View key={row.name} style={s.legendRow}>
          <View style={[s.dot, { backgroundColor: row.name === '未分類' ? '#A1A8AC' : COLORS[index % COLORS.length] }]} />
          <Text style={s.category}>{row.name}</Text>
          <Text style={s.count}>{row.count} 項</Text>
        </View>)}
      </View>
      <Text style={s.note}>{hasMultipleFunctions
        ? '一項添加物可屬多類，分類項次合計可能大於添加物總數；長條依分類項次占比呈現，不代表含量或風險高低。'
        : '依資料庫功能分類；長條表示分類數量占比，不代表含量或風險高低。'}
        {rows.some(row => row.name === '未分類') ? '「未分類」表示已比對到添加物，但缺少功能分類資料。' : ''}
      </Text>
    </> : <Text style={s.empty}>本次未比對到添加物，不代表食品一定不含添加物，請確認包裝標示。</Text>}
    <DetailButton title={additiveCount ? `查看 ${additiveCount} 項添加物詳情` : '查看成分詳情'} onPress={onDetails} />
  </View>;
}

const styles = (scale: number) => StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  heading: { flex: 1, minWidth: 0, gap: 7 },
  title: { color: C.text, fontSize: 18 * scale, fontWeight: '800', flexShrink: 1 },
  subtitle: { color: C.muted, fontSize: 12 * scale, lineHeight: 20 * scale },
  total: { alignItems: 'flex-end', gap: 2 },
  number: { color: C.text, fontSize: 31 * scale, lineHeight: 35 * scale, fontWeight: '800', fontVariant: ['tabular-nums'] },
  totalLabel: { color: C.muted, fontSize: 11 * scale },
  sectionLabel: { color: '#505D63', fontSize: 13 * scale, fontWeight: '600', marginTop: 22, marginBottom: 12 },
  bar: { flexDirection: 'row', height: 16, borderRadius: 9, overflow: 'hidden', backgroundColor: '#EEF1F1' },
  legend: { gap: 15, marginTop: 17, marginBottom: 17 },
  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dot: { width: 11, height: 11, borderRadius: 4 },
  category: { flex: 1, color: '#3A474D', fontSize: 14 * scale, lineHeight: 22 * scale },
  count: { color: '#657177', fontSize: 13 * scale, fontVariant: ['tabular-nums'] },
  note: { color: C.muted, fontSize: 11 * scale, lineHeight: 19 * scale },
  empty: { color: C.muted, fontSize: 13 * scale, lineHeight: 22 * scale, marginTop: 18 },
});
