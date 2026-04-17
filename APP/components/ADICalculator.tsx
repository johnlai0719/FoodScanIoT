import React from 'react';
import { View, Text, StyleSheet, TextInput } from 'react-native';
import { Scale, Info } from 'lucide-react-native';

interface ADICalculatorProps {
  weight: number;
  onWeightChange: (weight: number) => void;
  adiValue?: number;
}

export function ADICalculator({ weight, onWeightChange, adiValue }: ADICalculatorProps) {
  if (!adiValue) return null;

  const dailyLimit = weight * adiValue;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Scale size={16} color="#10b981" />
        <Text style={styles.headerText}>ADI 計算器</Text>
      </View>

      <View style={styles.inputGroup}>
        <Text style={styles.label}>您的體重 (KG)</Text>
        <TextInput
          style={styles.input}
          keyboardType="numeric"
          value={String(weight)}
          onChangeText={(text) => onWeightChange(Number(text))}
        />
      </View>

      <View style={styles.divider} />

      <View style={styles.resultRow}>
        <View>
          <Text style={styles.label}>每日安全攝取上限</Text>
          <Text style={styles.resultValue}>
            {dailyLimit.toFixed(1)} <Text style={{fontSize: 12}}>mg</Text>
          </Text>
        </View>
      </View>

      <View style={styles.infoBox}>
        <Info size={12} color="#10b981" />
        <Text style={styles.infoText}>
          每日耐受量 (ADI) 是指一個人在一生中每天攝入某種食品添加劑而不會產生健康風險的估計量。
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: 20, padding: 16, backgroundColor: '#18181b', borderRadius: 16 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 },
  headerText: { color: '#fff', fontSize: 14, fontWeight: 'bold' },
  inputGroup: { marginBottom: 16 },
  label: { fontSize: 10, color: '#71717a', fontWeight: 'bold', marginBottom: 4, letterSpacing: 1 },
  input: { backgroundColor: '#27272a', borderRadius: 8, padding: 12, color: '#fff', fontSize: 14 },
  divider: { height: 1, backgroundColor: '#27272a', marginBottom: 16 },
  resultRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' },
  resultValue: { color: '#10b981', fontSize: 24, fontWeight: 'bold' },
  infoBox: { flexDirection: 'row', gap: 8, backgroundColor: 'rgba(16, 185, 129, 0.1)', padding: 12, borderRadius: 8, marginTop: 16 },
  infoText: { flex: 1, fontSize: 10, color: '#10b981', lineHeight: 16 }
});
