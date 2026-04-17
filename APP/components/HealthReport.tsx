import React from 'react';
import { ShieldCheck, AlertTriangle } from 'lucide-react-native';
// 引入 Tailwind 包裝組件
import { View, Text } from '../src/tw';
import { ScanResult } from '../types';

export const HealthReport: React.FC<{ result: ScanResult }> = ({ result }) => {
  const { final_health_diagnosis, product_info } = result;
  const { grade, score, summary, warnings } = final_health_diagnosis;

  const getGradeColorClass = (g: string) => {
    switch (g) {
      case 'A': return 'border-emerald-500 text-emerald-500 bg-emerald-500';
      case 'B': return 'border-lime-500 text-lime-500 bg-lime-500';
      case 'C': return 'border-yellow-500 text-yellow-500 bg-yellow-500';
      case 'D': return 'border-orange-500 text-orange-500 bg-orange-500';
      default: return 'border-red-500 text-red-500 bg-red-500';
    }
  };

  const gradeClasses = getGradeColorClass(grade);
  const colorHex = gradeClasses.includes('emerald') ? '#10b981' : 
                   gradeClasses.includes('lime') ? '#84cc16' :
                   gradeClasses.includes('yellow') ? '#eab308' :
                   gradeClasses.includes('orange') ? '#f97316' : '#ef4444';

  return (
    <View style={{ marginBottom: 20 }}>
      <View style={{ marginBottom: 16 }}>
        <Text style={{ fontSize: 12, fontWeight: '700', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 1 }}>{product_info.brand}</Text>
        <Text style={{ fontSize: 24, fontWeight: '700', color: '#111827', marginTop: 4 }}>{product_info.name}</Text>
      </View>

      <View className={`flex-row justify-between items-center p-5 bg-white rounded-3xl border border-b-4 ${gradeClasses.split(' ')[0]}`}>
        <View>
          <Text style={{ fontSize: 12, fontWeight: '700', color: '#6b7280' }}>健康評分</Text>
          <Text style={{ fontSize: 36, fontWeight: '700', color: colorHex }}>{score}</Text>
        </View>
        <View className={`w-16 h-16 rounded-full items-center justify-center ${gradeClasses.split(' ')[2]}`}>
          <Text style={{ color: 'white', fontSize: 28, fontWeight: '700' }}>{grade}</Text>
        </View>
      </View>

      <View style={{ marginTop: 20, padding: 16, backgroundColor: '#ecfdf5', borderRadius: 16, borderLeftWidth: 4, borderLeftColor: '#10b981' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <ShieldCheck size={18} color="#10b981" />
          <Text style={{ fontSize: 14, fontWeight: '700', color: '#047857' }}>臨床診斷總結</Text>
        </View>
        <Text selectable style={{ fontSize: 14, color: '#374151', lineHeight: 24 }}>{summary}</Text>
      </View>

      {warnings && warnings.length > 0 && (
        <View style={{ marginTop: 12, gap: 8 }}>
          {warnings.map((w, idx) => (
            <View key={idx} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#fef2f2', padding: 12, borderRadius: 12 }}>
              <AlertTriangle size={16} color="#ef4444" />
              <Text selectable style={{ fontSize: 12, fontWeight: '700', color: '#b91c1c' }}>{w}</Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
};
