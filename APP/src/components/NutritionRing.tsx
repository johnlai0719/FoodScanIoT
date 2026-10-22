import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { AnalysisResponse } from '../types';
import { useFontScale } from '../contexts/FontScaleContext';
import { getResultPresentation, getNutritionRingSegments } from '../utils/resultPresentation';

export default function NutritionRing({ result, size = 160 }: { result: AnalysisResponse; size?: number }) {
  const { fontScale } = useFontScale();
  const model = getResultPresentation(result);
  const circumference = 2 * Math.PI * 46;
  const segments = getNutritionRingSegments(result);
  let accumulated = 0;
  return <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}
    accessible accessibilityLabel={`Nutri-Score ${model.grade ? model.grade + ' 級' : '分級未提供'}，${model.gradeStyle.label}。圓環顏色說明見旁邊圖例。`}>
    <Svg width={size} height={size} viewBox="0 0 110 110" style={StyleSheet.absoluteFill}>
      <Circle cx={55} cy={55} r={46} fill="none" stroke="#EBEEEE" strokeWidth={9} />
      {segments.length > 0 ? segments.map((item, index) => {
        const fraction = item.fraction;
        const offset = accumulated;
        accumulated += fraction;
        return <Circle key={`${item.key ?? item.reason}-${index}`} cx={55} cy={55} r={46}
          fill="none" stroke={item.color} strokeWidth={9}
          strokeDasharray={`${fraction * circumference} ${circumference}`}
          strokeDashoffset={-offset * circumference} transform="rotate(-90 55 55)" />;
      }) : model.grade && <Circle cx={55} cy={55} r={46} fill="none" stroke={model.gradeStyle.color} strokeWidth={9} />}
    </Svg>
    <View style={{ width: size * 0.72, alignItems: 'center', gap: 6 }}>
      <Text style={{ fontSize: Math.min(30 * fontScale, size * 0.22), color: model.gradeStyle.color, fontWeight: '800' }}>{model.grade ? `${model.grade} 級` : '未分級'}</Text>
      <Text style={{ fontSize: Math.min(12 * fontScale, size * 0.083), lineHeight: 18 * fontScale, color: model.gradeStyle.color, fontWeight: '700', textAlign: 'center' }}>{model.gradeStyle.label}</Text>
      <Text style={{ fontSize: 9 * fontScale, color: '#737B80' }}>Nutri-Score</Text>
    </View>
  </View>;
}
