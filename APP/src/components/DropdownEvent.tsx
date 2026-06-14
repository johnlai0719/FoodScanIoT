import React, { useState } from 'react';
import { View, Text, Pressable, StyleSheet, Linking, LayoutAnimation, Platform, UIManager } from 'react-native';
import { Calendar, ChevronUp, ChevronDown, ExternalLink, Landmark, Newspaper, Users } from 'lucide-react-native';
import { FoodSafetyEvent, SourceLink } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';

if (Platform.OS === 'android') {
  UIManager.setLayoutAnimationEnabledExperimental?.(true);
}

interface Props {
  event: FoodSafetyEvent;
  index: number;
}

const SOURCE_TIERS = [
  { key: 'official' as const, label: '官方', Icon: Landmark, color: '#0097A7', bg: '#E6F7EE', border: '#5EC4CC' },
  { key: 'news'     as const, label: '新聞', Icon: Newspaper, color: '#b45309', bg: '#fffbeb', border: '#fde68a' },
  { key: 'social'   as const, label: '社群', Icon: Users,     color: '#6d28d9', bg: '#f5f3ff', border: '#ddd6fe' },
];

function normalizeLinks(raw: SourceLink[] | string | null | undefined, fallbackTitle: string): SourceLink[] {
  if (!raw) return [];
  if (typeof raw === 'string') {
    let title = fallbackTitle;
    try { title = new URL(raw).hostname.replace(/^www\./, ''); } catch {}
    return [{ title, url: raw }];
  }
  return raw;
}

function SourceSection({ styles, event }: { styles: ReturnType<typeof createStyles>; event: FoodSafetyEvent }) {
  const hasSources = event.sources && Object.values(event.sources).some(v => !!v);

  if (hasSources) {
    return (
      <View style={styles.sourceContainer}>
        {SOURCE_TIERS.map(({ key, label, Icon, color, bg, border }) => {
          const links = normalizeLinks(event.sources?.[key], `${label}來源`);
          if (links.length === 0) return null;
          return (
            <View key={key} style={[styles.sourceTier, { backgroundColor: bg, borderColor: border }]}>
              <View style={styles.sourceTierHeader}>
                <Icon size={11} color={color} />
                <Text style={[styles.sourceTierLabel, { color }]}>{label}來源</Text>
              </View>
              <View style={styles.sourceLinkList}>
                {links.map((link, i) => (
                  <Pressable key={i} onPress={() => Linking.openURL(link.url)} style={styles.sourceLink}>
                    <Text style={[styles.sourceLinkText, { color }]} numberOfLines={1}>{link.title}</Text>
                    <ExternalLink size={10} color={color} />
                  </Pressable>
                ))}
              </View>
            </View>
          );
        })}
      </View>
    );
  }

  // 舊格式相容
  const legacyLinks = event.links ?? [];
  const hasLegacy = legacyLinks.length > 0 || !!event.source_url;
  if (!hasLegacy) return null;

  return (
    <View style={styles.footer}>
      <Text style={styles.footerLabel}>資料審核：衛福部食藥署不合格食品庫</Text>
      <View style={styles.links}>
        {legacyLinks.map((link, i) => (
          <Pressable key={i} onPress={() => Linking.openURL(link.url)} style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
            <Text style={styles.linkText}>{link.title}</Text>
            <ExternalLink size={10} color="#009B52" />
          </Pressable>
        ))}
        {legacyLinks.length === 0 && event.source_url && (
          <Pressable onPress={() => Linking.openURL(event.source_url!)} style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
            <Text style={styles.linkText}>官方來源紀錄</Text>
            <ExternalLink size={10} color="#009B52" />
          </Pressable>
        )}
      </View>
    </View>
  );
}

export default function DropdownEvent({ event, index }: Props) {
  const { fontScale } = useFontScale();
  const styles = createStyles(fontScale);
  const [isOpen, setIsOpen] = useState(index === 0);

  const isViolation = /違規|不合格|超標|罰|警示/.test(event.type);
  const isRoutine = /稽查|例行|核查|合格/.test(event.type);

  const badgeStyle = isRoutine ? styles.badgeGreen : isViolation ? styles.badgeRed : styles.badgeAmber;
  const badgeTextStyle = isRoutine ? styles.badgeTextGreen : isViolation ? styles.badgeTextRed : styles.badgeTextAmber;

  const toggle = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setIsOpen(v => !v);
  };

  return (
    <View style={[styles.card, isOpen && styles.cardOpen]}>
      <Pressable onPress={toggle} style={styles.header}>
        <View style={styles.headerMeta}>
          <View style={[styles.dateBadge, { flexDirection: 'row', alignItems: 'center', gap: 4 }]}>
            <Calendar size={10} color="#757575" />
            <Text style={styles.dateText}>{event.date}</Text>
          </View>
          <View style={[styles.typeBadge, badgeStyle]}>
            <Text style={[styles.typeText, badgeTextStyle]}>{event.type}</Text>
          </View>
        </View>
        <View style={styles.headerBottom}>
          <Text style={styles.title} numberOfLines={2}>{event.title}</Text>
          {isOpen ? <ChevronUp size={14} color="#757575" /> : <ChevronDown size={14} color="#757575" />}
        </View>
      </Pressable>

      {isOpen && (
        <View style={styles.body}>
          <Text style={styles.summary}>{event.summary}</Text>

          <SourceSection styles={styles} event={event} />
        </View>
      )}
    </View>
  );
}

const createStyles = (scale: number) => StyleSheet.create({
  card: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#DDDDDD',
    borderRadius: 16,
    overflow: 'hidden',
    marginBottom: 8,
  },
  cardOpen: {
    backgroundColor: '#FCFAF7',
    borderColor: '#C8B097',
  },
  header: { padding: 14, gap: 8 },
  headerMeta: { flexDirection: 'row', gap: 6, alignItems: 'center' },
  headerBottom: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 },
  dateBadge: {
    backgroundColor: '#FAF0E4',
    borderColor: '#DDDDDD',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  dateText: { fontSize: 10 * scale, color: '#757575', fontFamily: 'monospace' },
  typeBadge: { borderRadius: 8, paddingHorizontal: 7, paddingVertical: 3, borderWidth: 1 },
  badgeGreen: { backgroundColor: '#f0fdf4', borderColor: '#bbf7d0' },
  badgeRed: { backgroundColor: '#fff1f2', borderColor: '#fecdd3' },
  badgeAmber: { backgroundColor: '#fffbeb', borderColor: '#fde68a' },
  typeText: { fontSize: 9 * scale, fontWeight: '700', textTransform: 'uppercase' },
  badgeTextGreen: { color: '#15803d' },
  badgeTextRed: { color: '#b91c1c' },
  badgeTextAmber: { color: '#b45309' },
  title: { fontSize: 13 * scale, fontWeight: '700', color: '#1A1A1A', flex: 1 },
  body: {
    padding: 14,
    borderTopWidth: 1,
    borderTopColor: '#EFEADC',
    backgroundColor: '#fff',
    gap: 12,
  },
  summary: {
    fontSize: 12 * scale,
    color: '#1A1A1A',
    lineHeight: 18,
    fontWeight: '600',
    borderLeftWidth: 2,
    borderLeftColor: '#009B52',
    paddingLeft: 10,
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: '#EFEADC',
    paddingTop: 8,
    flexWrap: 'wrap',
    gap: 4,
  },
  footerLabel: { fontSize: 10 * scale, color: '#757575', flex: 1 },
  links: { flexDirection: 'row', gap: 8 },
  linkText: { fontSize: 10 * scale, color: '#009B52', fontWeight: '700' },

  // Three-tier sources
  sourceContainer: { gap: 6 },
  sourceTier: {
    borderRadius: 10, borderWidth: 1,
    paddingHorizontal: 10, paddingVertical: 8, gap: 6,
  },
  sourceTierHeader: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  sourceTierLabel: { fontSize: 10 * scale, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.4 },
  sourceLinkList: { gap: 4 },
  sourceLink: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  sourceLinkText: { fontSize: 11 * scale, fontWeight: '600', flex: 1 },
});
