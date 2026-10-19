import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Info, Plus, Minus } from 'lucide-react-native';
import { AnalysisResponse } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { formatNutritionValue, getResultPresentation } from '../utils/resultPresentation';
import NutritionRing from './NutritionRing';
import { ScreenHeader, reportColors as C, ui } from './reportUi';

export default function NutritionDetails({ result, onBack }: { result: AnalysisResponse; onBack: () => void }) {
  const { fontScale } = useFontScale();
  const s = useMemo(() => styles(fontScale), [fontScale]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const model = getResultPresentation(result);
  const penalty = model.penalties.reduce((sum, item) => sum + Math.abs(item.points), 0);
  const bonus = model.breakdown.filter(item => item.points > 0).reduce((sum, item) => sum + item.points, 0);
  return <View style={ui.stack}>
    <ScreenHeader title="營養評估" onBack={onBack} right={<Pressable style={ui.iconButton} accessibilityLabel="計分說明" accessibilityRole="button" aria-expanded={showInfo} accessibilityState={{ expanded: showInfo }} onPress={() => setShowInfo(!showInfo)}><Info size={20} color={C.text} /></Pressable>} />
    <View style={s.intro}><Text style={s.introText}>Nutri-Score 原始分數 {model.score ?? '未提供'}{model.grade ? ` 分（${model.grade} 級）` : ''}，分數越低越好。營養數值以每 100 公克／100 毫升為基準；未標示的欄位不會當作含量為零。</Text></View>
    {showInfo && <View style={ui.card}><Text style={s.description}>Nutri-Score 由扣分項目減去加分項目計算。飲料與一般食品的等級分界不同，等級採用本次分析結果。圓環為扣分項目的占比，不是健康百分比。</Text></View>}
    <View style={s.hero}>
      <NutritionRing result={result} size={160} />
      <View style={{ gap: 8, flexShrink: 1 }}>
        <View style={[s.grade, { backgroundColor: model.gradeStyle.background }]}><Text style={[s.gradeBig, { color: model.gradeStyle.color }]}>{model.grade ? `${model.grade} 級` : '未分級'}</Text><Text style={[s.gradeLabel, { color: model.gradeStyle.color }]}>{model.gradeStyle.label}</Text></View>
        <Text style={s.equation}>扣分 {penalty} − 加分 {bonus}</Text>
        <Text style={s.equation}>原始分數 {model.score ?? '—'}</Text>
      </View>
    </View>
    <View style={[ui.card, { padding: 0, overflow: 'hidden' }]}>
      {model.breakdown.length ? model.breakdown.map((item, index) => {
        const key = `${item.key ?? item.reason}-${index}`;
        const open = expanded === key;
        const isPenalty = item.points < 0;
        const magnitude = Math.abs(item.points);
        const max = item.maxPoints;
        const ratio = typeof max === 'number' && max > 0 ? Math.min(1, magnitude / max) : null;
        return <View key={key} style={[s.item, index > 0 && s.itemBorder]}>
          <Pressable accessibilityRole="button" accessibilityLabel={`${item.reason}，${isPenalty ? '扣' : '加'}${magnitude}分，查看說明`} aria-expanded={open} accessibilityState={{ expanded: open }} onPress={() => setExpanded(open ? null : key)}>
            <View style={s.itemRow}>
              <View style={{ flex: 1, gap: 8 }}>
                <View style={s.labelRow}><Text style={s.itemLabel}>{item.reason}</Text><Text style={s.value}>{formatNutritionValue(item.value)}{typeof item.value === 'number' ? ` ${item.unit ?? ''}` : ''}</Text></View>
                <View style={s.track}>{ratio !== null && <View style={[s.fill, { width: `${ratio * 100}%`, backgroundColor: isPenalty ? '#F55A1D' : '#23A66E' }]} />}</View>
              </View>
              <View style={[s.points, isPenalty && s.penalty]}><Text style={[s.pointNumber, isPenalty && { color: '#DE2B30' }]}>{item.points > 0 ? '+' : ''}{item.points}</Text><Text style={[s.pointMax, isPenalty && { color: '#C73C3C' }]}>{max == null ? '分' : `/ ${max}`}</Text></View>
              {open ? <Minus size={14} color={C.muted} /> : <Plus size={14} color={C.muted} />}
            </View>
          </Pressable>
          {open && <Text style={s.description}>{item.description || '本項目未提供進一步說明。'}{max == null ? ' 本次結果未提供此項計分上限。' : ''}</Text>}
        </View>;
      }) : <Text style={[s.description, { padding: 16 }]}>本次結果沒有提供逐項計分明細。</Text>}
    </View>
    <View style={s.footnote}><Info size={16} color={C.muted} /><Text style={s.footnoteText}>點選各項營養指標可查看說明。未標示不代表含量為零。</Text></View>
  </View>;
}

const styles = (scale: number) => StyleSheet.create({
  intro: { backgroundColor: '#EAF7EF', borderRadius: 13, borderWidth: 1, borderColor: '#D6ECDF', padding: 14 }, introText: { color: '#345345', fontSize: 13 * scale, lineHeight: 22 * scale },
  hero: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 22, paddingVertical: 15 },
  grade: { paddingHorizontal: 15, paddingVertical: 12, borderRadius: 12, gap: 4 }, gradeBig: { fontWeight: '800', fontSize: 25 * scale, textAlign: 'center' }, gradeLabel: { fontWeight: '700', fontSize: 12 * scale }, equation: { color: C.muted, fontSize: 12 * scale },
  item: { padding: 14, gap: 9 }, itemBorder: { borderTopWidth: 1, borderTopColor: '#ECEEEE' }, itemRow: { flexDirection: 'row', alignItems: 'center', gap: 9 }, labelRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 5 }, itemLabel: { color: C.text, fontSize: 13 * scale, fontWeight: '700', flexShrink: 1 }, value: { fontSize: 11 * scale, color: C.muted },
  track: { height: 7, borderRadius: 4, backgroundColor: '#F1F2F3', borderWidth: 1, borderColor: '#E1E4E4', overflow: 'hidden' }, fill: { height: '100%', borderRadius: 4 },
  points: { minWidth: 42, paddingVertical: 5, paddingHorizontal: 6, backgroundColor: '#F3F4F5', borderWidth: 1, borderColor: '#E4E6E7', borderRadius: 9, alignItems: 'center' }, penalty: { backgroundColor: '#FFF0F0', borderColor: '#F8D0D2' }, pointNumber: { color: '#616A70', fontWeight: '800', fontSize: 17 * scale }, pointMax: { fontSize: 10 * scale, color: '#7A8085' },
  description: { color: '#646F74', fontSize: 13 * scale, lineHeight: 22 * scale, marginTop: 4 }, footnote: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, padding: 10 }, footnoteText: { flex: 1, color: C.muted, fontSize: 12 * scale, lineHeight: 20 * scale },
});
