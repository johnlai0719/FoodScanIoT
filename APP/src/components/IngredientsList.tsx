import React, { useState } from 'react';
import { View, Text, Pressable, StyleSheet, LayoutAnimation, Platform, UIManager, Linking } from 'react-native';
import { ChevronUp, ChevronDown, ExternalLink } from 'lucide-react-native';
import { IngredientDetail } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';

if (Platform.OS === 'android') {
  UIManager.setLayoutAnimationEnabledExperimental?.(true);
}

const GROUP_ZH: Record<string, string> = {
  pregnant: '孕婦',
  child: '嬰幼兒/兒童',
  kidney_disease: '慢性腎臟病患者',
  asthma: '氣喘患者',
  diabetes: '糖尿病族群',
  hypertension: '高血壓族群',
  allergy: '過敏體質者',
};

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
              </View>
              {isExpanded ? <ChevronUp size={14} color="#757575" /> : <ChevronDown size={14} color="#757575" />}
            </Pressable>

            {isExpanded && (
              <View style={styles.body}>
                <Text style={styles.description}>{ing.description || '一般成分，無特定化學危害紀錄。'}</Text>

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
                        • {GROUP_ZH[r.group] ?? r.group}：{r.reason}
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
    gap: 8,
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
