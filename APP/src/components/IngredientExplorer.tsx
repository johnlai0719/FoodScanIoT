import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { ChevronRight, CircleHelp, Search, X } from 'lucide-react-native';
import { IngredientDetail } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { isMatchedAdditive } from '../utils/resultPresentation';
import IngredientsList from './IngredientsList';
import type { IngredientTab } from './ResultOverview';
import { ScreenHeader, reportColors as C, ui } from './reportUi';

export default function IngredientExplorer({ ingredients, initialTab = 'all', onBack, onNavigate }: {
  ingredients: IngredientDetail[]; initialTab?: IngredientTab; onBack: () => void; onNavigate?: () => void;
}) {
  const { fontScale } = useFontScale();
  const s = useMemo(() => styles(fontScale), [fontScale]);
  const [tab, setTab] = useState<IngredientTab>(initialTab);
  const [selected, setSelected] = useState<IngredientDetail | null>(null);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState('');
  const additives = ingredients.filter(isMatchedAdditive);
  const unmatched = ingredients.filter(item => !isMatchedAdditive(item));
  const lists = { all: ingredients, additives, unmatched };
  const list = lists[tab].filter(item => `${item.name} ${(item.category ?? []).join(' ')}`.toLowerCase().includes(query.trim().toLowerCase()));
  if (selected) return <View style={ui.stack}>
    <ScreenHeader title="成分詳情" onBack={() => { setSelected(null); onNavigate?.(); }} />
    <View style={[ui.card, { borderColor: isMatchedAdditive(selected) ? '#F5CDCE' : C.border }]}>
      <IngredientsList key={selected.name} ingredients={[selected]} />
    </View>
    <Text style={s.note}>{isMatchedAdditive(selected) ? '請依包裝標示與可追溯來源理解成分用途與注意事項。' : '未比對到資料庫不代表此成分安全或具有危險性，請以包裝標示與可追溯來源為準。'}</Text>
  </View>;
  return <View style={ui.stack}>
    <ScreenHeader title={tab === 'additives' ? '添加物分析' : tab === 'unmatched' ? '未比對成分' : '成分分析'} onBack={onBack}
      right={<Pressable accessibilityRole="button" accessibilityLabel={searching ? '關閉成分搜尋' : '搜尋成分'} style={ui.iconButton} onPress={() => { setSearching(!searching); setQuery(''); }}>{searching ? <X size={19} color={C.text} /> : <Search size={19} color={C.text} />}</Pressable>} />
    <View style={s.tabs}>{[
      { key: 'all' as const, title: '全部', count: ingredients.length },
      { key: 'additives' as const, title: '添加物', count: additives.length },
      { key: 'unmatched' as const, title: '未比對', count: unmatched.length },
    ].map(item => <Pressable key={item.key} accessibilityRole="tab" aria-selected={tab === item.key} accessibilityState={{ selected: tab === item.key }} onPress={() => setTab(item.key)} style={[s.tab, tab === item.key && s.activeTab]}><Text style={[s.tabText, tab === item.key && s.activeTabText]}>{item.title} ({item.count})</Text></Pressable>)}</View>
    {searching && <View style={s.search}><Search size={17} color={C.muted} /><TextInput accessibilityLabel="搜尋成分名稱或類別" autoFocus placeholder="搜尋成分名稱或類別" placeholderTextColor="#8B9296" value={query} onChangeText={setQuery} style={s.searchInput} /></View>}
    {tab === 'unmatched' && <Text style={s.note}>共有 {unmatched.length} 項成分未比對到添加物資料庫，不代表具有危險性，也不代表一定不是添加物。</Text>}
    {tab === 'additives' && <Text style={s.note}>共比對到 {additives.length} 項添加物。點選成分可查看說明與資料來源。</Text>}
    <View style={[ui.card, { padding: 0, overflow: 'hidden' }]}>
      {list.map((item, index) => {
        const additive = isMatchedAdditive(item);
        return <Pressable key={`${item.name}-${index}`} accessibilityRole="button" accessibilityLabel={`查看${item.name}的詳細說明與來源`} onPress={() => { setSelected(item); onNavigate?.(); }} style={({ pressed }) => [s.row, index > 0 && s.divider, pressed && { backgroundColor: '#F0F8F3' }]}>
          {!additive && <CircleHelp size={18} color="#979DA1" strokeWidth={1.7} />}
          <View style={{ flex: 1, gap: 6 }}>
            {(additive || item.nearMiss) && <View style={s.badges}>
              {additive && <View style={s.additiveBadge}><Text style={s.additiveBadgeText}>添加物</Text></View>}
              {item.nearMiss && !additive && <View style={s.unknownBadge}><Text style={s.categoryText}>疑似・未確認</Text></View>}
              {(item.category ?? []).map(category => <View style={s.category} key={category}><Text style={s.categoryText}>{category}</Text></View>)}
            </View>}
            <Text style={s.name}>{item.name}</Text>
          </View>
          <ChevronRight size={17} color="#8D9599" />
        </Pressable>;
      })}
      {list.length === 0 && <View style={{ padding: 20, gap: 7 }}><Text style={s.empty}>{query ? '沒有符合的成分' : '本次沒有可列出的成分'}</Text><Text style={s.note}>{query ? '試試其他關鍵字。' : '辨識缺漏與實際未使用無法由空清單區分，請確認包裝標示。'}</Text></View>}
    </View>
    <Text style={s.note}>「添加物」表示已比對到資料庫；點選任一項可查看原有說明與來源。</Text>
  </View>;
}

const styles = (scale: number) => StyleSheet.create({
  tabs: { flexDirection: 'row', gap: 6, paddingBottom: 2 }, tab: { flex: 1, alignItems: 'center', justifyContent: 'center', borderRadius: 20, borderWidth: 1, borderColor: C.border, backgroundColor: '#FFFFFF', paddingVertical: 9, paddingHorizontal: 4 }, activeTab: { backgroundColor: C.green, borderColor: C.green }, tabText: { color: '#737C80', fontSize: 12 * scale, fontWeight: '600' }, activeTabText: { color: '#FFFFFF' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 15, minHeight: 56 }, divider: { borderTopWidth: 1, borderTopColor: '#EAECED' }, name: { color: '#273139', fontWeight: '600', fontSize: 14 * scale, lineHeight: 22 * scale },
  badges: { flexDirection: 'row', gap: 5, flexWrap: 'wrap' }, additiveBadge: { backgroundColor: '#FFEEEE', borderRadius: 5, paddingHorizontal: 6, paddingVertical: 3 }, additiveBadgeText: { color: '#D0393E', fontWeight: '700', fontSize: 10 * scale }, category: { backgroundColor: '#F0F1F2', borderRadius: 5, paddingHorizontal: 6, paddingVertical: 3 }, categoryText: { color: '#7C8085', fontSize: 10 * scale }, unknownBadge: { backgroundColor: '#FFF5D8', borderRadius: 5, paddingHorizontal: 6, paddingVertical: 3 },
  note: { color: '#7C8285', fontSize: 12 * scale, lineHeight: 21 * scale }, empty: { color: C.text, fontSize: 14 * scale, fontWeight: '600' }, search: { flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: 1, borderColor: C.border, borderRadius: 11, backgroundColor: '#FFFFFF', paddingHorizontal: 12 }, searchInput: { flex: 1, minHeight: 42, fontSize: 14 * scale, color: C.text },
});
