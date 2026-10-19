import React, { useMemo, useState } from 'react';
import { Image, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { AlertOctagon, ChartNoAxesColumnIncreasing, CheckCircle2, CircleHelp, Database,
  FileText, Flame, FlaskConical, Info, Package, ScanLine, ShieldCheck, Box, CookingPot } from 'lucide-react-native';
import { AnalysisResponse } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { formatAbsoluteTime, getDataFreshness } from '../utils/dataFreshness';
import { formatNutritionValue, getResultPresentation } from '../utils/resultPresentation';
import NutritionRing from './NutritionRing';
import AdditiveCategorySummary from './AdditiveCategorySummary';
import { DetailButton, ScreenHeader, reportColors as C, ui } from './reportUi';

export type IngredientTab = 'all' | 'additives' | 'unmatched';

interface Props {
  result: AnalysisResponse;
  photoUri?: string;
  isDemo?: boolean;
  matchedAllergens: string[];
  generalWarnings: string[];
  additionalWarnings?: React.ReactNode;
  onBack: () => void;
  onScanNext: () => void;
  onNutrition: () => void;
  onIngredients: (tab: IngredientTab) => void;
  onHistory?: () => void;
}

export default function ResultOverview({ result, photoUri, isDemo = false, matchedAllergens, generalWarnings,
  additionalWarnings, onBack, onScanNext, onNutrition, onIngredients, onHistory }: Props) {
  const { fontScale } = useFontScale();
  const { width } = useWindowDimensions();
  const s = useMemo(() => styles(fontScale), [fontScale]);
  const model = getResultPresentation(result);
  const freshness = getDataFreshness(result);
  const [info, setInfo] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const narrow = width < 370 || fontScale > 1.1;
  const company = [result.product_info?.brand, result.product_info?.manufacturer].filter(Boolean)
    .filter((name, i, all) => all.indexOf(name) === i).join(' ／ ');
  const facts = [
    { label: '熱量', value: model.nutrition.calories, unit: 'kcal', Icon: Flame, color: '#E7950F', bg: '#FFFAEF', border: '#F8ECD6' },
    { label: '糖', value: model.nutrition.sugar, unit: 'g', Icon: Box, color: '#397AD5', bg: '#F3F8FE', border: '#E0EBFA' },
    { label: '鈉', value: model.nutrition.sodium, unit: 'mg', Icon: CookingPot, color: '#CE383A', bg: '#FFF3F3', border: '#F8DFE0' },
  ];
  return <View style={ui.stack}>
    <ScreenHeader title="分析結果" onBack={onBack} />
    {isDemo && <View style={s.notice}><Info size={17} color="#A46D14" /><Text style={s.noticeText}>示範資料・僅供介面預覽，非本次食品的實際分析。</Text></View>}
    <View style={[ui.card, s.product]}>
      {photoUri && !imageFailed
        ? <Image source={{ uri: photoUri }} style={s.photo} accessibilityLabel="本次掃描的食品標示" onError={() => setImageFailed(true)} />
        : <View style={[s.photo, s.photoPlaceholder]}><Package size={30} color="#8BABA0" strokeWidth={1.5} /></View>}
      <View style={s.productMain}>
        <Text style={s.productName}>{result.product_info?.name || '未辨識到品名'}</Text>
        {!!company && <Text style={s.company}>{company}</Text>}
        <View style={s.productMeta}>
          <View style={[s.status, freshness.warning && s.staleStatus]}>
            <CheckCircle2 size={13} color={freshness.warning ? '#A46D14' : C.green} strokeWidth={2} />
            <Text style={[s.statusText, freshness.warning && { color: '#A46D14' }]}>{freshness.warning ? '快取待更新' : result.cached ? '快取結果' : '分析完成'}</Text>
          </View>
          <Text style={s.timestamp}>{formatAbsoluteTime(result.processed_at) ?? '更新時間不明'}</Text>
        </View>
      </View>
    </View>
    {!!freshness.warning && <View style={s.notice}><Info size={17} color="#A46D14" /><Text style={s.noticeText}>{freshness.warning}</Text></View>}
    {matchedAllergens.length > 0 && <View style={s.allergy} accessibilityRole="alert">
      <View style={ui.row}><AlertOctagon size={25} color="#FFFFFF" strokeWidth={1.8} /><Text style={s.allergyTitle}>個人化過敏原警示</Text></View>
      <Text style={s.allergyBody}>依你的設定，標示中出現以下過敏原或相關警語，請確認包裝標示。</Text>
      <View style={s.chips}>{matchedAllergens.map(name => <View key={name} style={s.allergyChip}><Text style={s.allergyChipText}>{name}</Text></View>)}</View>
    </View>}
    <View style={ui.card}>
      <View style={[ui.row, { flexWrap: 'wrap' }]}>
        <ChartNoAxesColumnIncreasing size={23} color={C.green} strokeWidth={2.4} />
        <Text style={s.title}>營養評估</Text>
        <Pressable style={s.infoButton} onPress={() => setInfo(!info)} accessibilityRole="button" accessibilityLabel="營養評估說明" aria-expanded={info} accessibilityState={{ expanded: info }}><Info size={17} color={C.muted} /></Pressable>
        <View style={[s.gradeLabel, { backgroundColor: model.gradeStyle.background }]}>
          <Text style={[s.gradeText, { color: model.gradeStyle.color }]}>{model.grade ? `${model.grade} 級・` : ''}{model.gradeStyle.label}</Text>
        </View>
      </View>
      {info && <Text style={s.help}>Nutri-Score 分數越低越好；等級依產品類別與後端計算結果顯示。圓環呈現各扣分項目的占比。</Text>}
      <View style={[s.nutrition, narrow && { flexDirection: 'column' }]}>
        <View style={s.ring}><NutritionRing result={result} size={narrow ? 132 : 120} /></View>
        <View style={[s.nutritionMain, narrow && { borderLeftWidth: 0, paddingLeft: 0, width: '100%' }]}>
          <Text style={s.reason}>{model.leadingPenalty ? `主要扣分：${model.leadingPenalty.reason}` : '營養標示與評級'}</Text>
          <Text style={s.description} numberOfLines={3}>{model.leadingPenalty?.description || '依產品營養標示計算。查看完整分析，了解各項營養數值與計分方式。'}</Text>
        </View>
      </View>
      <Text style={s.basis}>每 100 公克／100 毫升</Text>
      <View style={s.factRow}>{facts.map(({ label, value, unit, Icon, color, bg, border }) => <View key={label} style={[s.fact, { backgroundColor: bg, borderColor: border }]}>
        <View style={ui.row}><Icon size={16} color={color} strokeWidth={1.8} /><Text style={s.factLabel}>{label}</Text></View>
        <Text style={s.factValue}>{formatNutritionValue(value)}<Text style={s.factUnit}>{typeof value === 'number' && Number.isFinite(value) ? ` ${unit}` : ''}</Text></Text>
      </View>)}</View>
      <DetailButton title="查看完整營養分析" onPress={onNutrition} />
    </View>
    <View style={ui.card}>
      <View style={ui.row}><Database size={23} color={C.green} strokeWidth={1.8} /><Text style={s.title}>成分分析</Text></View>
      <View style={s.statRow}>{[
        { title: '成分', count: model.ingredients.length, key: 'all' as const, Icon: FileText, color: '#72533C', bg: '#FFFCF7', border: '#EFE3D5' },
        { title: '添加物', count: model.additives.length, key: 'additives' as const, Icon: FlaskConical, color: '#C52B2F', bg: '#FFF5F5', border: '#F8DDDD' },
        { title: '未比對', count: model.unmatched.length, key: 'unmatched' as const, Icon: CircleHelp, color: '#626C70', bg: '#F7F8F8', border: '#E6E9E9' },
      ].map(({ title, count, key, Icon, color, bg, border }) => <Pressable key={key} onPress={() => onIngredients(key)} accessibilityRole="button" accessibilityLabel={`查看${count}項${title}`} style={({ pressed }) => [s.stat, { backgroundColor: bg, borderColor: border }, pressed && { opacity: 0.65 }]}>
        <Icon size={21} color={color} strokeWidth={1.7} /><Text style={[s.statCount, { color }]}>{count}</Text><Text style={s.statName}>{title}</Text>
      </Pressable>)}</View>
      <Text style={s.ingredientNote}>{model.ingredients.length === 0 ? '尚未辨識到成分，請確認照片中的標示是否清楚。' : `已比對到 ${model.additives.length} 項添加物，其餘 ${model.unmatched.length} 項未比對到添加物資料庫。`}</Text>
      <DetailButton title="查看成分與來源" onPress={() => onIngredients('all')} />
    </View>
    <AdditiveCategorySummary ingredients={model.ingredients} onDetails={() => onIngredients(model.additives.length ? 'additives' : 'all')} />
    {generalWarnings.length > 0 && <View style={s.packageWarnings}>
      <View style={ui.row}><ShieldCheck size={17} color="#85704D" /><Text style={s.warningTitle}>包裝標示提醒</Text></View>
      {generalWarnings.map((warning, i) => <Text key={i} style={s.warningText}>{warning}</Text>)}
    </View>}
    {additionalWarnings}
    {!!onHistory && <DetailButton title="查看廠商食安事件" onPress={onHistory} />}
    <View style={s.notice}><Info size={18} color="#9F6A2B" /><Text style={s.noticeText}>本報告依食品標示與資料庫產生，僅供資訊參考。</Text></View>
    <Pressable onPress={onScanNext} accessibilityRole="button" style={({ pressed }) => [s.scanNext, pressed && { opacity: 0.8 }]}><ScanLine size={24} color="#FFFFFF" strokeWidth={1.8} /><Text style={s.scanNextText}>掃描下一項產品</Text></Pressable>
  </View>;
}

const styles = (scale: number) => StyleSheet.create({
  product: { flexDirection: 'row', gap: 13, alignItems: 'center' },
  photo: { width: 76, height: 84, borderRadius: 11, backgroundColor: '#EDF3EF' },
  photoPlaceholder: { alignItems: 'center', justifyContent: 'center' },
  productMain: { flex: 1, minWidth: 0, gap: 5 }, productName: { color: C.text, fontWeight: '800', fontSize: 17 * scale, lineHeight: 24 * scale },
  company: { color: C.muted, fontSize: 13 * scale, lineHeight: 19 * scale }, productMeta: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginTop: 2 },
  status: { flexDirection: 'row', alignItems: 'center', gap: 4, backgroundColor: C.mint, paddingHorizontal: 7, paddingVertical: 4, borderRadius: 8 },
  staleStatus: { backgroundColor: '#FFF3D9' }, statusText: { color: C.green, fontWeight: '700', fontSize: 11 * scale }, timestamp: { color: C.muted, fontSize: 11 * scale },
  allergy: { backgroundColor: '#E52229', padding: 17, gap: 10, borderRadius: 15, borderWidth: 1, borderColor: '#D81920' },
  allergyTitle: { color: '#FFFFFF', fontSize: 19 * scale, fontWeight: '800', flex: 1 }, allergyBody: { color: '#FFF8F8', fontSize: 13 * scale, lineHeight: 21 * scale },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 }, allergyChip: { backgroundColor: '#EC5558', borderColor: '#FF9FA2', borderWidth: 1, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 5 }, allergyChipText: { color: '#FFFFFF', fontWeight: '700', fontSize: 13 * scale },
  title: { color: C.text, fontWeight: '800', fontSize: 18 * scale, flexGrow: 1 }, infoButton: { padding: 4 },
  gradeLabel: { paddingHorizontal: 8, paddingVertical: 5, borderRadius: 9, marginLeft: 'auto' }, gradeText: { fontSize: 11 * scale, fontWeight: '700' },
  help: { fontSize: 12 * scale, lineHeight: 19 * scale, color: C.muted, marginTop: 10 }, nutrition: { flexDirection: 'row', alignItems: 'center', gap: 13, marginTop: 14 },
  ring: { alignItems: 'center', justifyContent: 'center' }, nutritionMain: { flex: 1, minWidth: 0, borderLeftWidth: 1, borderLeftColor: C.border, paddingLeft: 13 },
  reason: { fontSize: 14 * scale, lineHeight: 21 * scale, fontWeight: '800', color: C.text, marginBottom: 5 }, description: { color: '#73777A', fontSize: 12 * scale, lineHeight: 19 * scale },
  basis: { color: C.muted, fontSize: 10 * scale, marginTop: 10, marginBottom: 7 }, factRow: { flexDirection: 'row', gap: 7 },
  fact: { flex: 1, minWidth: 0, borderRadius: 10, paddingVertical: 9, paddingHorizontal: 8, borderWidth: 1, gap: 5 },
  factLabel: { color: '#50595F', fontSize: 12 * scale }, factValue: { fontSize: 18 * scale, color: C.text, fontWeight: '800', fontVariant: ['tabular-nums'] }, factUnit: { fontSize: 11 * scale, fontWeight: '400', color: '#62686B' },
  statRow: { flexDirection: 'row', gap: 8, marginTop: 13 }, stat: { flex: 1, borderRadius: 11, borderWidth: 1, alignItems: 'center', paddingVertical: 11, gap: 4 }, statCount: { fontSize: 25 * scale, fontWeight: '800', fontVariant: ['tabular-nums'] }, statName: { color: '#767B7E', fontSize: 12 * scale },
  ingredientNote: { color: '#767B7E', fontSize: 12 * scale, lineHeight: 20 * scale, marginTop: 10 },
  notice: { flexDirection: 'row', alignItems: 'flex-start', gap: 9, backgroundColor: '#FFFBEE', borderWidth: 1, borderColor: '#F4D78C', borderRadius: 12, padding: 11 }, noticeText: { flex: 1, color: '#8C623A', fontSize: 12 * scale, lineHeight: 19 * scale },
  packageWarnings: { backgroundColor: '#F2F5F2', padding: 13, gap: 7, borderRadius: 12 }, warningTitle: { color: '#72644F', fontSize: 13 * scale, fontWeight: '700' }, warningText: { color: '#777568', fontSize: 12 * scale, lineHeight: 20 * scale },
  scanNext: { minHeight: 51, borderRadius: 16, backgroundColor: C.teal, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 10, marginBottom: 10 }, scanNextText: { fontSize: 17 * scale, fontWeight: '800', color: '#FFFFFF' },
});
