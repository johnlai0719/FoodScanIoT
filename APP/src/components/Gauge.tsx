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
  /**
   * Cloud 算出的 Nutri-Score 等級（A–E）。**有給就用它，不要自己從分數推。**
   *
   * 2026-09-13 修：`score` 是 Nutri-Score 原始分數（**越低越好**，約 −15~40），
   * 這裡卻拿它跟 0–100 的門檻比（A 要 ≥80），於是每一筆都判錯——
   * raw −5（最健康）落到 E、raw 35（最糟）反而落到 D。
   *
   * 而且等級也**不能**由呈現端自己推：飲料與純水另有一套帶
   * （飲料要 ≤2 才 B、只有純水可能 A），只有 Cloud 的 `get_grade()` 知道。
   */
  grade?: string | null;
  label: string;
  type: 'health' | 'additives';
  subtitle?: string;
  pos?: number;
  neg?: number;
  healthSegments?: HealthSegment[];
  /** 加分項，畫成內圈。長度是「加分 ÷ 扣分」——它抵銷了多少。 */
  bonusSegments?: HealthSegment[];
}

const RADIUS = 45;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
// 內圈畫加分。半徑差 11 是「兩圈看得出是兩圈、但不至於把中間的字擠掉」。
const INNER_RADIUS = 34;
const INNER_CIRCUMFERENCE = 2 * Math.PI * INNER_RADIUS;

const NUTRI_GRADES = [
  { grade: 'A', color: '#038141', label: '優秀 (A)', min: 80 },
  { grade: 'B', color: '#85bb2f', label: '優良 (B)', min: 60 },
  { grade: 'C', color: '#fecb02', label: '中等偏高 (C)', min: 40 },
  { grade: 'D', color: '#ee8100', label: '負荷偏高 (D)', min: 20 },
  { grade: 'E', color: '#e63e11', label: '極高負擔 (E)', min: 0 },
];

/**
 * 等級在圓弧上佔多少。A 幾乎滿圈、E 最短——這是**視覺強度**不是精確比例，
 * 因為 Nutri-Score 的分數帶本來就不等寬（固體 C 是 3~10、D 是 11~18）。
 * 要精確數字的人看詳細頁的逐項得分，那裡才是真的算式。
 */
function gradeArcFraction(grade: string): number {
  return { A: 0.92, B: 0.74, C: 0.56, D: 0.38, E: 0.2 }[grade] ?? 0.2;
}

function getNutriGrade(score: number) {
  return NUTRI_GRADES.find(g => score >= g.min) ?? NUTRI_GRADES[4];
}

export default function Gauge({ score, grade, label, type, subtitle, pos = 3, neg = 8, healthSegments, bonusSegments }: GaugeProps) {
  // 等級以 Cloud 給的為準；沒給才退回舊的推法（舊快取裡的結果沒有這個欄位）。
  const activeGrade =
    NUTRI_GRADES.find(g => g.grade === grade) ?? getNutriGrade(score);
  let accumulatedPercent = 0;
  let accumulatedBonus = 0;

  // 弧的顏色跟等級同一個來源，不要再用一組分數門檻——那正是上面那個 bug。
  const gaugeColor = activeGrade.color;

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
          {/* 內圈：加分抵銷了多少扣分。沒有加分時整圈不畫——
              畫一條 0 長度的弧只是多一個看不見的元素。 */}
          {type === 'health' && bonusSegments && bonusSegments.length > 0 && (
            <>
              <Circle
                cx="50" cy="50" r={INNER_RADIUS}
                fill="transparent"
                stroke="rgba(62,47,40,0.05)"
                strokeWidth="5"
              />
              {bonusSegments.map((seg, idx) => {
                const segLen = (seg.percentage / 100) * INNER_CIRCUMFERENCE;
                const rotation = -90 + (accumulatedBonus / 100) * 360;
                accumulatedBonus += seg.percentage;
                return (
                  <Circle
                    key={`b${idx}`}
                    cx="50" cy="50" r={INNER_RADIUS}
                    fill="transparent"
                    stroke={seg.color}
                    strokeWidth="5"
                    strokeDasharray={`${segLen} ${INNER_CIRCUMFERENCE}`}
                    transform={`rotate(${rotation} 50 50)`}
                  />
                );
              })}
            </>
          )}
          {type === 'health' && healthSegments && healthSegments.length > 0 ? (
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
              // 弧長照**等級**在 A–E 五段中的位置，不是 score/100——
              // score 是 Nutri-Score 原始分數，不是百分比。
              strokeDasharray={`${gradeArcFraction(activeGrade.grade) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
              transform="rotate(-90 50 50)"
            />
          )}
        </Svg>

        <View style={styles.centerText}>
          {type === 'health' ? (
            <>
              {/* 中央放等級，不放分數。Nutri-Score 的主體是 A–E；
                  原始分數放在下面那行，並註明方向——不寫「越低越好」的話，
                  「10 分」會被讀成 0–100 裡的 10 分。 */}
              {/* 圓環本身表達的是**扣分的組成**，所以中央放總分；
                  等級縮小放在下面，不再是主角——只有一個字母的圓沒有資訊。 */}
              <Text style={styles.scoreBig}>{score}</Text>
              <Text style={styles.subtext}>
                {activeGrade.grade} 級・分數越低越好
              </Text>
            </>
          ) : (
            <>
              <Text style={styles.scoreText}>{subtitle ?? '0'}</Text>
              <Text style={styles.subtext}>添加物</Text>
            </>
          )}
        </View>
      </View>

      {type === 'health' && ((healthSegments?.length ?? 0) > 0 || (bonusSegments?.length ?? 0) > 0) && (
        <View style={styles.legend}>
          {healthSegments?.map((seg, idx) => (
            <View key={idx} style={styles.legendRow}>
              <View style={[styles.legendDot, { backgroundColor: seg.color }]} />
              <Text style={styles.legendName}>{seg.name}</Text>
              <Text style={styles.legendValue}>{seg.valueText}</Text>
            </View>
          ))}
          {/* 加分項的點畫成空心，跟外圈的扣分區分開——同樣的實心點會讓人
              以為它們在同一個圓上。 */}
          {bonusSegments?.map((seg, idx) => (
            <View key={`b${idx}`} style={styles.legendRow}>
              <View style={[styles.legendDotHollow, { borderColor: seg.color }]} />
              <Text style={styles.legendName}>{seg.name}</Text>
              <Text style={styles.legendValue}>{seg.valueText}</Text>
            </View>
          ))}
        </View>
      )}

      {/* 全部 0 分：圓環是空的，要講清楚那是「沒有扣分」而不是「沒算出來」。 */}
      {type === 'health' && (healthSegments?.length ?? 0) === 0 && (
        <Text style={styles.emptyNote}>各項營養素皆未達扣分門檻。</Text>
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
  legendDotHollow: { width: 8, height: 8, borderRadius: 999, borderWidth: 2, backgroundColor: 'transparent' },
  emptyNote: { fontSize: 11, color: '#757575', textAlign: 'center', marginTop: 8 },
  scoreBig: { fontSize: 30, fontWeight: '900', lineHeight: 34, fontVariant: ['tabular-nums'] },
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
