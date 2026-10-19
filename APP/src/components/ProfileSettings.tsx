import React, { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Check } from 'lucide-react-native';
import { UserConditions } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { ScreenHeader, reportColors as C, ui } from './reportUi';

export interface ProfileValues {
  group: UserConditions['group']; allergens: string[]; chronic: string[];
  customAllergen: string; dietary: string[];
}
const GROUPS: { key: UserConditions['group']; label: string }[] = [
  { key: 'adult', label: '一般成人' }, { key: 'pregnant', label: '孕婦／哺乳' }, { key: 'child', label: '幼童' },
];
const CHRONIC = [{ key: 'hypertension', label: '高血壓' }, { key: 'diabetes', label: '高血糖' }, { key: 'hyperlipidemia', label: '高血脂' }];
const ALLERGENS = ['甲殼類', '芒果', '花生', '牛奶', '蛋', '堅果', '芝麻', '含麩質穀物', '大豆', '魚類', '亞硫酸鹽'];
const DIETARY = ['素食', '清真', '低糖', '低鈉'];

export default function ProfileSettings({ initial, onBack, onSave }: {
  initial: ProfileValues; onBack: () => void; onSave: (values: ProfileValues) => Promise<void>;
}) {
  const { fontScale } = useFontScale();
  const s = useMemo(() => styles(fontScale), [fontScale]);
  const [draft, setDraft] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const toggle = (key: 'allergens' | 'chronic' | 'dietary', value: string) => setDraft(previous => ({ ...previous,
    [key]: previous[key].includes(value) ? previous[key].filter(item => item !== value) : [...previous[key], value] }));
  const save = async () => {
    setSaving(true); setError('');
    try { await onSave(draft); } catch { setError('設定儲存失敗，請再試一次。'); } finally { setSaving(false); }
  };
  return <View style={ui.stack}>
    <ScreenHeader title="個人健康設定" onBack={onBack} right={<Pressable onPress={save} disabled={saving} accessibilityRole="button" accessibilityLabel="儲存個人健康設定" style={ui.iconButton}>{saving ? <ActivityIndicator color={C.green} /> : <Text style={s.save}>儲存</Text>}</Pressable>} />
    <Text style={s.intro}>根據你的健康狀況提供個人化提醒，設定後可隨時修改。</Text>
    {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
    <View style={s.section}><Text style={s.label}>1. 身分族群</Text><View style={s.wrap}>{GROUPS.map(item => <Pressable key={item.key} accessibilityRole="radio" aria-checked={draft.group === item.key} accessibilityState={{ checked: draft.group === item.key }} onPress={() => setDraft(previous => ({ ...previous, group: item.key }))} style={[s.chip, s.groupChip, draft.group === item.key && s.groupSelected]}><Text style={[s.chipText, draft.group === item.key && { color: '#FFFFFF' }]}>{item.label}</Text></Pressable>)}</View></View>
    <View style={s.section}><Text style={s.label}>2. 慢性病 <Text style={s.optional}>（可多選）</Text></Text><View style={s.wrap}>{CHRONIC.map(item => <Pressable key={item.key} accessibilityRole="checkbox" aria-checked={draft.chronic.includes(item.key)} accessibilityState={{ checked: draft.chronic.includes(item.key) }} onPress={() => toggle('chronic', item.key)} style={[s.chip, draft.chronic.includes(item.key) && s.selected]}><Text style={[s.chipText, draft.chronic.includes(item.key) && s.selectedText]}>{item.label}</Text></Pressable>)}</View></View>
    <View style={s.section}><Text style={s.label}>3. 過敏原 <Text style={s.optional}>（可多選）</Text></Text><View style={s.wrap}>{ALLERGENS.map(item => <Pressable key={item} accessibilityRole="checkbox" aria-checked={draft.allergens.includes(item)} accessibilityState={{ checked: draft.allergens.includes(item) }} onPress={() => toggle('allergens', item)} style={[s.chip, draft.allergens.includes(item) && s.allergenSelected]}>{draft.allergens.includes(item) && <Check size={13} color="#E22B34" strokeWidth={2.5} />}<Text style={[s.chipText, draft.allergens.includes(item) && s.allergenText]}>{item}</Text></Pressable>)}</View>
      <TextInput value={draft.customAllergen} onChangeText={customAllergen => setDraft(previous => ({ ...previous, customAllergen }))} placeholder="其他過敏原（選填）" placeholderTextColor="#9BA3A6" style={s.custom} accessibilityLabel="其他過敏原" />
    </View>
    <View style={s.section}><Text style={s.label}>4. 其他飲食偏好 <Text style={s.optional}>（選填）</Text></Text><View style={s.wrap}>{DIETARY.map(item => <Pressable key={item} accessibilityRole="checkbox" aria-checked={draft.dietary.includes(item)} accessibilityState={{ checked: draft.dietary.includes(item) }} onPress={() => toggle('dietary', item)} style={[s.chip, draft.dietary.includes(item) && s.selected]}><Text style={[s.chipText, draft.dietary.includes(item) && s.selectedText]}>{item}</Text></Pressable>)}</View><Text style={s.preferenceNote}>偏好僅作個人紀錄，不影響營養評分，也不代表產品符合認證。</Text></View>
    <View style={s.privacy}><Text style={s.privacyText}>健康設定儲存在這台裝置上，個人化提醒不改變產品的 Nutri-Score。</Text></View>
  </View>;
}

const styles = (scale: number) => StyleSheet.create({
  save: { color: C.green, fontSize: 14 * scale, fontWeight: '800' }, intro: { color: '#758086', fontSize: 14 * scale, lineHeight: 23 * scale, marginBottom: 5 }, section: { gap: 12, marginBottom: 13 }, label: { color: C.text, fontSize: 15 * scale, fontWeight: '800' }, optional: { color: '#879095', fontWeight: '400', fontSize: 13 * scale }, wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 9 },
  chip: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4, paddingVertical: 10, paddingHorizontal: 15, minHeight: 40, backgroundColor: '#EAF6EF', borderColor: '#CEE4D8', borderWidth: 1, borderRadius: 10 }, groupChip: { flexGrow: 1 }, chipText: { fontSize: 14 * scale, color: '#737D79', fontWeight: '600' }, groupSelected: { backgroundColor: C.green, borderColor: C.green }, selected: { backgroundColor: '#DDF4E8', borderColor: '#75CBA4' }, selectedText: { color: '#008950', fontWeight: '800' }, allergenSelected: { backgroundColor: '#FFF1F2', borderColor: '#F77980' }, allergenText: { color: '#DC2A35', fontWeight: '800' }, custom: { minHeight: 43, borderBottomWidth: 1, borderBottomColor: C.border, fontSize: 13 * scale, color: C.text, paddingVertical: 8 }, preferenceNote: { color: '#8A9593', fontSize: 11 * scale, lineHeight: 19 * scale }, privacy: { borderRadius: 12, backgroundColor: '#EAF6EE', borderColor: '#D3E8DA', borderWidth: 1, padding: 13, marginTop: 8 }, privacyText: { color: '#698071', fontSize: 12 * scale, lineHeight: 21 * scale }, error: { color: '#C92C35', fontSize: 13 * scale },
});
