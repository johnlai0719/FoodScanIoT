import React, { useState } from 'react';
import { View, Text, Pressable, StyleSheet, LayoutAnimation, Platform, UIManager, Linking } from 'react-native';
import { ChevronUp, ChevronDown, ExternalLink } from 'lucide-react-native';
import { IngredientDetail } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { groupLabel } from '../constants/groupVocabulary';

if (Platform.OS === 'android') {
  UIManager.setLayoutAnimationEnabledExperimental?.(true);
}

interface Props {
  ingredients: IngredientDetail[];
}

export default function IngredientsList({ ingredients }: Props) {
  const { fontScale } = useFontScale();
  const styles = createStyles(fontScale);
  const [expandedIndex, setExpandedIndex] = useState<number | null>(0);

  const toggle = (idx: number) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setExpandedIndex(expandedIndex === idx ? null : idx);
  };

  return (
    <View style={styles.container}>
      {ingredients.map((ing, idx) => {
        const isAdditive = ing.isAdditive === true || ing.isAdditive === 'true';
        const isExpanded = expandedIndex === idx;

        return (
          <View key={idx} style={[styles.card, isAdditive ? styles.cardAdditive : styles.cardNormal]}>
            <Pressable onPress={() => toggle(idx)} style={styles.header}>
              <View style={styles.headerLeft}>
                <Text style={[styles.name, isAdditive && styles.nameAdditive]}>{ing.name}</Text>
                {isAdditive && (
                  <View style={styles.additiveBadge}>
                    <Text style={styles.additiveBadgeText}>添加物</Text>
                  </View>
                )}
                {/* 官方類別徽章。放在「添加物」徽章後面而不是取代它——
                    「添加物」是判定結果，類別是這項判定的細分，兩者不同層級。
                    一項可能屬多類（己二烯酸鉀是防腐劑也是殺菌劑），全列出來。 */}
                {isAdditive && (ing.category ?? []).map(c => (
                  <View key={c} style={styles.categoryBadge}>
                    <Text style={styles.categoryBadgeText}>{c}</Text>
                  </View>
                ))}
                {/* 疑似：OCR 讀到的字配不到，但有很接近的項目。
                    徽章刻意寫「疑似」而不是添加物名——它不是判定。 */}
                {!isAdditive && ing.nearMiss && (
                  <View style={styles.nearMissBadge}>
                    <Text style={styles.nearMissBadgeText}>疑似</Text>
                  </View>
                )}
              </View>
              {isExpanded ? <ChevronUp size={14} color="#757575" /> : <ChevronDown size={14} color="#757575" />}
            </Pressable>

            {isExpanded && (
              <View style={styles.body}>
                <Text style={styles.description}>{ing.description || '一般成分，無特定化學危害紀錄。'}</Text>

                {/* 掃到的原文與疑似對象**並列**。只給疑似對象就是替換，
                    那正是被否決的字典後修正；只給原文則使用者無從查證。
                    兩個都給，判斷權留給使用者。 */}
                {!isAdditive && ing.nearMiss && (
                  <View style={styles.nearMissBox}>
                    <Text style={styles.nearMissLine}>
                      掃到：<Text style={styles.nearMissMono}>{ing.name}</Text>
                    </Text>
                    <Text style={styles.nearMissLine}>
                      資料庫最接近：<Text style={styles.nearMissMono}>{ing.nearMiss.officialName}</Text>
                      （差 {ing.nearMiss.distance} 字／共 {ing.nearMiss.scannedLength} 字）
                    </Text>
                    <Text style={styles.nearMissNote}>
                      可能是辨識誤差，也可能真的是不同的東西。本系統未將它計入添加物，請以包裝標示為準。
                    </Text>
                  </View>
                )}

                {(ing.iarcRating || ing.adiValue) && (
                  <View style={styles.badgeRow}>
                    {ing.iarcRating && ing.iarcRating !== '無' && ing.iarcRating !== '未分類' && (
                      <View style={styles.iarcBadge}>
                        <Text style={styles.iarcText}>IARC: {ing.iarcRating}</Text>
                      </View>
                    )}
                    {ing.adiValue && ing.adiValue !== '無' && (
                      <View style={styles.adiBadge}>
                        <Text style={styles.adiText}>ADI: {ing.adiValue}</Text>
                      </View>
                    )}
                  </View>
                )}

                {ing.purpose && (
                  <View style={styles.purposeBox}>
                    <Text style={styles.purposeText}>主要作用：{ing.purpose}</Text>
                  </View>
                )}

                {ing.groupRisks && ing.groupRisks.length > 0 && (
                  <View style={styles.risksBox}>
                    <Text style={styles.risksTitle}>可能受體風險警示 (特定族群)</Text>
                    {ing.groupRisks.map((r, rid) => (
                      <Text key={rid} style={styles.riskItem}>
                        • {groupLabel(r.group)}：{r.reason}
                      </Text>
                    ))}
                  </View>
                )}

                {isAdditive && ing.description_sources && ing.description_sources.length > 0 && (
                  <View style={styles.sourcesBox}>
                    <Text style={styles.sourcesTitle}>資料來源</Text>
                    {ing.description_sources.map((src, sid) => (
                      <Pressable
                        key={sid}
                        style={styles.sourceLink}
                        onPress={() => Linking.openURL(src)}
                      >
                        <ExternalLink size={11} color="#0369a1" />
                        <Text style={styles.sourceLinkText} numberOfLines={1}>{src}</Text>
                      </Pressable>
                    ))}
                  </View>
                )}
              </View>
            )}
          </View>
        );
      })}
    </View>
  );
}

const createStyles = (scale: number) => StyleSheet.create({
  container: { gap: 8 },
  card: {
    borderRadius: 12,
    borderWidth: 1,
    overflow: 'hidden',
  },
  cardNormal: {
    backgroundColor: '#fff',
    borderColor: '#DDDDDD',
  },
  cardAdditive: {
    backgroundColor: 'rgba(254,242,242,0.6)',
    borderColor: '#fecaca',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 14,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    // 類別徽章之後這一列可能有三、四個標籤，長名稱（「品質改良用、釀造用
    // 及食品製造用劑」）加上去會擠爆單行。必須換行，不可讓它裁掉——
    // 裁掉的話使用者看不出還有別的類別。
    flexWrap: 'wrap',
    gap: 6,
    flex: 1,
  },
  name: {
    fontSize: 13 * scale,
    fontWeight: '600',
    color: '#1A1A1A',
    flexShrink: 1,
  },
  nameAdditive: {
    color: '#991b1b',
    fontWeight: '700',
  },
  // 類別徽章：描邊不填色，與「添加物」的實心徽章分層——實心是判定，
  // 描邊是判定的細分。同色系可以讓人看出兩者相關，明度差別看出主從。
  categoryBadge: {
    borderWidth: 1, borderColor: '#D9A6A6', borderRadius: 4,
    paddingHorizontal: 5, paddingVertical: 1,
  },
  categoryBadgeText: { fontSize: 9, fontWeight: '700', color: '#9b5c5c' },
  // 疑似：琥珀色，與添加物（實心判定）和一般成分（無標記）都分得開。
  nearMissBadge: {
    paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6,
    backgroundColor: '#FEF3C7', borderWidth: 1, borderColor: '#FCD34D',
  },
  nearMissBadgeText: { fontSize: 9 * scale, fontWeight: '800', color: '#B45309' },
  nearMissBox: {
    marginTop: 8, padding: 10, borderRadius: 8,
    backgroundColor: '#FEF3C7', borderWidth: 1, borderColor: '#FCD34D', gap: 3,
  },
  nearMissLine: { fontSize: 11 * scale, color: '#1A1A1A', lineHeight: 16 },
  nearMissMono: { fontWeight: '700' },
  nearMissNote: { fontSize: 10 * scale, color: '#B45309', lineHeight: 15, marginTop: 2 },
  additiveBadge: {
    backgroundColor: '#fee2e2',
    borderColor: '#fecaca',
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  additiveBadgeText: {
    fontSize: 9 * scale,
    fontWeight: '700',
    color: '#b91c1c',
    textTransform: 'uppercase',
  },
  body: {
    padding: 14,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#DDDDDD',
    gap: 10,
  },
  description: {
    fontSize: 12 * scale,
    color: '#1A1A1A',
    lineHeight: 18,
  },
  badgeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  iarcBadge: {
    backgroundColor: '#E6F7EE',
    borderColor: '#DDDDDD',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  iarcText: { fontSize: 10 * scale, color: '#1A1A1A', fontWeight: '600' },
  adiBadge: {
    backgroundColor: '#E6F7EE',
    borderColor: '#5EC4CC',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  adiText: { fontSize: 10 * scale, color: '#0097A7', fontWeight: '600' },
  purposeBox: {
    backgroundColor: 'rgba(245,236,226,0.6)',
    borderColor: '#E2E8F0',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  purposeText: { fontSize: 11 * scale, color: '#757575', fontWeight: '600' },
  risksBox: {
    backgroundColor: '#fff1f2',
    borderColor: '#fecdd3',
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    gap: 4,
  },
  risksTitle: {
    fontSize: 9 * scale,
    fontWeight: '800',
    color: '#dc2626',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  riskItem: {
    fontSize: 11 * scale,
    color: '#7f1d1d',
    lineHeight: 16,
  },
  sourcesBox: {
    borderTopWidth: 1,
    borderTopColor: '#DDDDDD',
    paddingTop: 8,
    gap: 4,
  },
  sourcesTitle: {
    fontSize: 9 * scale,
    fontWeight: '800',
    color: '#757575',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  sourceLink: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  sourceLinkText: {
    fontSize: 10 * scale,
    color: '#0369a1',
    textDecorationLine: 'underline',
    flex: 1,
  },
});
