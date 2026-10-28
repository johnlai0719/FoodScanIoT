import { StyleSheet, Text, View } from 'react-native';
import type { AnalysisResponse } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { getNutritionRingSegments, getResultPresentation } from '../utils/resultPresentation';
import { reportColors as C } from './reportUi';

export default function NutritionLegend({ result }: { result: AnalysisResponse }) {
  const { fontScale } = useFontScale();
  const segments = getNutritionRingSegments(result);
  const model = getResultPresentation(result);
  return <View style={styles.container}>
    <Text style={[styles.title, { fontSize: 12 * fontScale }]}>圓環顏色說明</Text>
    {segments.map((item, index) => <View key={`${item.key ?? item.reason}-${index}`} style={styles.row}>
      <View style={[styles.dot, { backgroundColor: item.color }]} />
      <View style={styles.label}>
        <Text style={{ fontSize: 12 * fontScale, lineHeight: 18 * fontScale, color: '#46545B', fontWeight: '600' }}>{item.reason}</Text>
        <Text style={{ fontSize: 10 * fontScale, color: C.muted }}>扣 {Math.abs(item.points)} 分</Text>
      </View>
    </View>)}
    {!segments.length && <>
      <View style={styles.row}>
        <View style={[styles.dot, { backgroundColor: model.grade ? model.gradeStyle.color : '#EBEEEE' }]} />
        <Text style={[styles.label, { color: C.muted, fontSize: 12 * fontScale, lineHeight: 18 * fontScale }]}>{model.grade ? `${model.grade} 級・等級色彩` : '尚無分級資料'}</Text>
      </View>
      <Text style={{ color: C.muted, fontSize: 11 * fontScale, lineHeight: 18 * fontScale }}>{model.breakdown.length ? '本次沒有扣分項目。' : '本次未提供逐項計分明細。'}</Text>
    </>}
    {segments.length > 0 && <Text style={{ fontSize: 10 * fontScale, color: C.muted, lineHeight: 17 * fontScale }}>弧長表示各項占總扣分的比例，並非營養含量或健康百分比。</Text>}
  </View>;
}

const styles = StyleSheet.create({
  container: { gap: 9 },
  title: { fontWeight: '700', color: '#52616A', marginBottom: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  dot: { width: 10, height: 10, borderRadius: 3 },
  label: { flex: 1, minWidth: 0, gap: 2 },
});
