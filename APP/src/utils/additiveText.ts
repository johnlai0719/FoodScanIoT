/**
 * 添加物說明的分層：第一眼只給「該注意什麼」，其餘收在「詳細說明」裡。
 *
 * 由來：2026-09-22 教授反映添加物詳情太長，但仍要有介紹。實際量過知識庫
 * （804 筆）後，`description` 的寫法相當固定，是二到五句的組合：
 *
 *   1. 定義       「亞硝酸鈉是一種常用於肉品加工的保色劑與防腐劑。」
 *   2. 用途範圍   「它廣泛添加於香腸、培根、貢丸等肉製品中…」
 *   3. 安全性評估 「儘管過量攝取有健康風險，但合理限量下使用非常安全…」
 *   4. 注意事項   「法規明文禁止使用於生鮮食材中…」
 *
 * 第 1 句講的「這是什麼」已經由畫面上的類別徽章（防腐劑、著色劑…）表達，
 * 文字再講一次是重複。真正有價值的是第 3、4 句。
 *
 * **為什麼用詞彙判斷而不是取第 3、4 句**：句數不是固定的——實測 1 句 1 筆、
 * 2 句 40 筆、3 句 338 筆、4 句 390 筆、5 句 35 筆。按位置取會在 379 筆
 * 不足四句的資料上抽到定義句。改以詞彙挑出評估／注意那一類句子後，平均
 * 從 127 字降到 67 字，而且留下來的是對的內容。
 *
 * ⚠ **這一層只做挑選，不改寫也不生成。** 顯示的每一個字都是知識庫裡的原句，
 *   所以不會出現「摘要把限制條件摘掉」這種事。要縮更短就得生成新句子，
 *   那是改動知識庫內容，不在這一層做。
 */

/** 判定「這句在講安全性評估或注意事項」的詞彙。 */
const ESSENTIAL_HINTS = [
  '安全', '安心', '評估', '風險', '危害', '擔心', '注意',
  '避免', '過量', '適量', '禁止', '限量', '敏感', '無害',
];

/**
 * 切句。
 *
 * 全形句號是主要分隔；半角句點也收，因為資料裡混用了（`醋酸澱粉` 那筆的
 * 第一句結尾是 `穩定劑.`）。但半角句點**夾在數字之間時不切**——限量數值
 * 寫成 `0.07 g/kg`，照切會把一個數字裂成兩句。
 */
export function splitSentences(text: string): string[] {
  if (!text) return [];
  return text
    .replace(/(?<!\d)\.(?!\d)/g, '。')
    .split('。')
    .map(s => s.trim())
    .filter(Boolean);
}

/**
 * 挑出第一眼要顯示的句子。
 *
 * 一句都挑不中時退回**最後一句**而不是第一句：資料裡這種情形有 8 筆，
 * 它們的收尾仍是評估語氣（`丙酸鈣` 的「適量攝取即可」、`氫氧化鈉溶液` 的
 * 「大眾可安心食用」），只是用詞不在上面的清單裡。退回第一句會剛好拿到
 * 那句被徽章取代掉的定義。
 *
 * 完全沒有句子可挑（空字串、或知識庫裡的殘留值）時回空陣列，由呼叫端決定
 * 顯示什麼——這一層不編造替代文字。
 */
export function pickEssentialSentences(description: string): string[] {
  const sentences = splitSentences(description);
  if (sentences.length === 0) return [];
  const hit = sentences.filter(s => ESSENTIAL_HINTS.some(h => s.includes(h)));
  return hit.length > 0 ? hit : [sentences[sentences.length - 1]];
}

/** 第一眼顯示的文字。句子之間補回句號，讀起來才是完整的話。 */
export function essentialDescription(description: string): string {
  const picked = pickEssentialSentences(description);
  return picked.length > 0 ? picked.join('。') + '。' : '';
}

/**
 * 「詳細說明」裡要補的句子——也就是第一眼沒顯示的那些。
 *
 * 回空陣列代表全文都已經顯示過了（2 句且兩句都命中的那類），此時呼叫端
 * 不該再給一個點不出東西的「詳細說明」入口。
 */
export function remainingSentences(description: string): string[] {
  const all = splitSentences(description);
  const shown = new Set(pickEssentialSentences(description));
  return all.filter(s => !shown.has(s));
}
