import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Circle } from 'react-native-svg';

interface HealthSegment {
  name: string;
  percentage: number;
  color: string;
  valueText: string;
}

interface GaugeProps {
  score: number;
  label: string;
  type: 'health' | 'additives';
  subtitle?: string;
  pos?: number;
  neg?: number;
  healthSegments?: HealthSegment[];
}

const RADIUS = 45;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const NUTRI_GRADES = [
  { grade: 'A', color: '#038141', label: '優秀 (A)', min: 80 },
  { grade: 'B', color: '#85bb2f', label: '優良 (B)', min: 60 },
  { grade: 'C', color: '#fecb02', label: '中等偏高 (C)', min: 40 },
  { grade: 'D', color: '#ee8100', label: '負荷偏高 (D)', min: 20 },
  { grade: 'E', color: '#e63e11', label: '極高負擔 (E)', min: 0 },
];

function getNutriGrade(score: number) {
  return NUTRI_GRADES.find(g => score >= g.min) ?? NUTRI_GRADES[4];
}

export default function Gauge({ score, label, type, subtitle, pos = 3, neg = 8, healthSegments }: GaugeProps) {
  const activeGrade = getNutriGrade(score);
  let accumulatedPercent = 0;

  const gaugeColor = score > 50 ? '#ef4444' : score > 0 ? '#f59e0b' : '#10b981';

  return (
    <View style={styles.card}>
      <View style={styles.svgContainer}>
        <Svg width={112} height={112} viewBox="0 0 100 100">
          <Circle
            cx="50" cy="50" r={RADIUS}
            fill="transparent"
            stroke="rgba(62,47,40,0.08)"
            strokeWidth="9"
          />
          {type === 'health' && healthSegments ? (
            healthSegments.map((seg, idx) => {
              const segLen = (seg.percentage / 100) * CIRCUMFERENCE;
              const rotation = -90 + (accumulatedPercent / 100) * 360;
              accumulatedPercent += seg.percentage;
              return (
                <Circle
                  key={idx}
                  cx="50" cy="50" r={RADIUS}
                  fill="transparent"
                  stroke={seg.color}
                  strokeWidth="9"
                  strokeDasharray={`${segLen} ${CIRCUMFERENCE}`}
                  transform={`rotate(${rotation} 50 50)`}
                />
              );
            })
          ) : (
            <Circle
              cx="50" cy="50" r={RADIUS}
              fill="transparent"
              stroke={gaugeColor}
              strokeWidth="8"
              strokeDasharray={`${(score / 100) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
              transform="rotate(-90 50 50)"
            />
          )}
        </Svg>

        <View style={styles.centerText}>
          {type === 'health' ? (
            <>
              <Text style={styles.scoreText}>{score} 分</Text>
              <Text style={styles.subtext}>配方評分</Text>
            </>
          ) : (
            <>
              <Text style={styles.scoreText}>{subtitle ?? '0'}</Text>
              <Text style={styles.subtext}>添加物</Text>
            </>
          )}
        </View>
      </View>

      {type === 'health' && healthSegments && (
        <View style={styles.legend}>
          {healthSegments.map((seg, idx) => (
            <View key={idx} style={styles.legendRow}>
              <View style={[styles.legendDot, { backgroundColor: seg.color }]} />
              <Text style={styles.legendName}>{seg.name}</Text>
              <Text style={styles.legendValue}>{seg.valueText}</Text>
            </View>
          ))}
        </View>
      )}

      <Text style={styles.labelText}>{label}</Text>

      {type === 'health' && (
        <View style={styles.ribbon}>
          {NUTRI_GRADES.map(({ grade, color }) => {
            const isMatch = activeGrade.grade === grade;
            return (
              <View
                key={grade}
                style={[
                  styles.ribbonItem,
                  { backgroundColor: isMatch ? color : '#f3f4f6', opacity: isMatch ? 1 : 0.4 },
                ]}
              >
                <Text style={{ color: isMatch ? '#fff' : '#9ca3af', fontWeight: 'bold', fontSize: 10 }}>
                  {grade}
                </Text>
              </View>
            );
          })}
          <Text style={styles.activeGradeLabel}>{activeGrade.label}</Text>
        </View>
      )}

      {type === 'health' && (
        <View style={styles.balanceContainer}>
          <View style={styles.balanceLeft}>
            <View style={[styles.dot, { backgroundColor: '#ef4444' }]} />
            <Text style={styles.balanceText}>負擔: +{neg}分</Text>
          </View>
          <Text style={styles.scaleTxt}>⚖️ 互抵</Text>
          <View style={styles.balanceRight}>
            <View style={[styles.dot, { backgroundColor: '#10b981' }]} />
            <Text style={styles.balanceText}>扣抵: -{pos}分</Text>
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: '#DDDDDD',
    alignItems: 'center',
    marginVertical: 8,
  },
  svgContainer: {
    position: 'relative',
    width: 112,
    height: 112,
    justifyContent: 'center',
    alignItems: 'center',
  },
  legend: {
    flexDirection: 'row',
    justifyContent: 'center',
    flexWrap: 'wrap',
    gap: 10,
    marginTop: 10,
  },
  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  legendName: {
    fontSize: 11,
    fontWeight: '700',
    color: '#1A1A1A',
  },
  legendValue: {
    fontSize: 10,
    color: '#757575',
    marginLeft: 2,
  },
  centerText: {
    position: 'absolute',
    alignItems: 'center',
  },
  scoreText: {
    fontSize: 16,
    fontWeight: '900',
    color: '#1A1A1A',
    marginTop: 2,
  },
  subtext: {
    fontSize: 8,
    color: '#757575',
    textTransform: 'uppercase',
    fontWeight: '600',
  },
  labelText: {
    marginTop: 10,
    fontSize: 12,
    fontWeight: 'bold',
    color: '#1A1A1A',
  },
  ribbon: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F5F5F5',
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderRadius: 12,
    marginTop: 10,
    borderWidth: 1,
    borderColor: '#DDDDDD',
  },
  ribbonItem: {
    width: 20,
    height: 20,
    borderRadius: 4,
    justifyContent: 'center',
    alignItems: 'center',
    marginHorizontal: 1.5,
  },
  activeGradeLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: '#757575',
    marginLeft: 8,
  },
  balanceContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#F5F5F5',
    borderWidth: 1,
    borderColor: '#DDDDDD',
    borderRadius: 12,
    padding: 8,
    width: '100%',
    marginTop: 12,
  },
  balanceLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fef2f2',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  balanceRight: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#E6F7EE',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 4,
  },
  balanceText: {
    fontSize: 11,
    fontWeight: 'bold',
    color: '#1A1A1A',
  },
  scaleTxt: {
    fontSize: 10,
    fontWeight: '900',
    color: '#757575',
  },
});
