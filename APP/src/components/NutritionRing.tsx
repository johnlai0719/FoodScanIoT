import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { AnalysisResponse } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { getResultPresentation, PENALTY_COLORS } from '../utils/resultPresentation';

export default function NutritionRing({ result, size = 126 }: { result: AnalysisResponse; size?: number }) {
  const { fontScale } = useFontScale();
  const model = getResultPresentation(result);
  const circumference = 2 * Math.PI * 46;
  const total = model.penalties.reduce((sum, item) => sum + Math.abs(item.points), 0);
  let accumulated = 0;
  return <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}
    accessible accessibilityLabel={`Nutri-Score ${model.score ?? '未提供'} 分，${model.grade ? model.grade + ' 級' : '分級未提供'}，分數越低越好`}>
    <Svg width={size} height={size} viewBox="0 0 110 110" style={StyleSheet.absoluteFill}>
      <Circle cx={55} cy={55} r={46} fill="none" stroke="#EBEEEE" strokeWidth={9} />
      {total > 0 ? model.penalties.map((item, index) => {
        const fraction = Math.abs(item.points) / total;
        const offset = accumulated;
        accumulated += fraction;
        return <Circle key={`${item.key ?? item.reason}-${index}`} cx={55} cy={55} r={46}
          fill="none" stroke={PENALTY_COLORS[item.key ?? ''] ?? model.gradeStyle.color} strokeWidth={9}
          strokeDasharray={`${fraction * circumference} ${circumference}`}
          strokeDashoffset={-offset * circumference} transform="rotate(-90 55 55)" />;
      }) : model.grade && <Circle cx={55} cy={55} r={46} fill="none" stroke={model.gradeStyle.color} strokeWidth={9} />}
    </Svg>
    <Text style={{ fontSize: Math.min(34 * fontScale, size * 0.29), color: '#121719', fontWeight: '800', fontVariant: ['tabular-nums'] }}>{model.score ?? '—'}</Text>
    <Text style={{ fontSize: 11 * fontScale, color: '#646B70' }}>Nutri-Score</Text>
    <Text style={{ fontSize: 9 * fontScale, color: '#737B80', marginTop: 2 }}>分數越低越好</Text>
  </View>;
}
