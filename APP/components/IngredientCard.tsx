import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Info } from 'lucide-react-native';
// 引入 Tailwind 包裝組件
import { View, Text, Pressable } from '../src/tw';
import { Ingredient } from '../types';

export const IngredientCard: React.FC<{ ingredient: Ingredient }> = ({ ingredient }) => {
  const [expanded, setExpanded] = useState(false);

  const getRiskColor = (level?: number) => {
    if (!level) return 'bg-gray-400';
    if (level >= 4) return 'bg-red-500';
    if (level >= 3) return 'bg-orange-500';
    return 'bg-emerald-500';
  };

  const getRiskColorHex = (level?: number) => {
    if (!level) return '#9ca3af';
    if (level >= 4) return '#ef4444';
    if (level >= 3) return '#f97316';
    return '#10b981';
  };

  return (
    <Pressable 
      className={`p-4 mb-3 rounded-2xl border ${ingredient.isAdditive ? 'bg-red-50/30 border-red-100' : 'bg-white border-gray-100'}`}
      onPress={() => setExpanded(!expanded)}
    >
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ fontSize: 16, fontWeight: '700', color: ingredient.isAdditive ? '#7f1d1d' : '#111827' }}>
            {ingredient.name}
          </Text>
          {ingredient.isAdditive && (
            <View style={{ backgroundColor: '#fee2e2', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 4 }}>
              <Text style={{ fontSize: 10, fontWeight: '700', color: '#b91c1c' }}>添加物</Text>
            </View>
          )}
        </View>
        {expanded ? <ChevronUp size={18} color="#9ca3af" /> : <ChevronDown size={18} color="#9ca3af" />}
      </View>

      {expanded && (
        <View style={{ marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: 'rgba(243,244,246,0.5)' }}>
          <Text selectable style={{ fontSize: 14, color: '#4b5563', lineHeight: 20, marginBottom: 12 }}>{ingredient.description || "一般成分，無特定風險紀錄。"}</Text>
          
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
            {ingredient.iarcRating && (
              <View style={{ backgroundColor: '#f3f4f6', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, borderLeftWidth: 3, borderLeftColor: '#6b7280' }}>
                <Text style={{ fontSize: 10, color: '#374151', fontWeight: 'bold' }}>IARC: {ingredient.iarcRating}</Text>
              </View>
            )}
            {ingredient.adiValue && (
              <View style={{ backgroundColor: '#eff6ff', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, borderLeftWidth: 3, borderLeftColor: '#3b82f6' }}>
                <Text style={{ fontSize: 10, color: '#1e40af', fontWeight: 'bold' }}>ADI: {ingredient.adiValue}</Text>
              </View>
            )}
          </View>

          {ingredient.purpose && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <Info size={14} color="#6b7280" />
              <Text style={{ fontSize: 12, color: '#6b7280' }}>用途：{ingredient.purpose}</Text>
            </View>
          )}

          {ingredient.groupRisks && ingredient.groupRisks.length > 0 && (
            <View style={{ backgroundColor: '#fef2f2', padding: 12, borderRadius: 12 }}>
              <Text style={{ fontSize: 10, fontWeight: '700', color: '#991b1b', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>特定族群風險</Text>
              {ingredient.groupRisks.map((risk, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: 8, marginBottom: 6 }}>
                  <View style={{ width: 6, height: 6, borderRadius: 3, marginTop: 6, backgroundColor: getRiskColorHex(risk.riskLevel) }} />
                  <Text selectable style={{ fontSize: 12, color: '#7f1d1d', flex: 1, lineHeight: 16 }}>
                    <Text style={{ fontWeight: '700' }}>{risk.group}：</Text>
                    {risk.reason}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </View>
      )}
    </Pressable>
  );
};
