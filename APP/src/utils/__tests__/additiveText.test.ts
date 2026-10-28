import {
  splitSentences,
  pickEssentialSentences,
  essentialDescription,
  remainingSentences,
} from '../additiveText';

// 全部取自正式知識庫（additives.description），不是編的——這一層的正確性
// 取決於真實資料的寫法，用假造的句子測等於沒測。
const 亞硝酸鈉 =
  '亞硝酸鈉是一種常用於肉品加工的保色劑與防腐劑。它廣泛添加於香腸、培根、貢丸等肉製品中，' +
  '主要用於保持外觀紅潤並抑制細菌繁殖。儘管過量攝取有健康風險，但合理限量下使用非常安全，' +
  '且能防範肉毒桿菌中毒。法規明文禁止使用於生鮮食材中，消費者應適量食用各類加工肉品。';

const 檸檬酸 =
  '檸檬酸是常見的食品調味劑與酸度調節劑，天然存在於柑橘類水果中。它常用於飲料、果醬與糖果中，' +
  '以提供清爽酸味並協助防腐。經國際食品安全機構評估，檸檬酸在一般食品合理使用下安全無虞，' +
  '亦是人體自然代謝產物。需要注意的是，頻繁且大量食用高酸性食品，可能會對牙齒琺瑯質造成侵蝕。';

// 半角句點：這筆的第一句結尾是「穩定劑.」
const 醋酸澱粉 =
  '醋酸澱粉是一種經過醋酸化改性的修飾澱粉，具有優良的凍融穩定性，在食品中主要作為粘稠增稠劑與穩定劑.' +
  '常見於冷凍食品、麵包製品、布丁果凍及即食麵中。經國際食品安全機構評估其安全性，' +
  '在合規限量的安全標準下正常飲食食用對健康無害，日常膳食正常的極低用量消費者不需擔心。';

// 詞彙清單一個都沒命中的那 8 筆之一
const 丙酸鈣 =
  '丙酸鈣（Calcium Propionate）是一種常規烘焙食品防腐劑，廣泛添加於麵包、吐司和糕點中，' +
  '用以抑制黴菌與細菌生長並延長保鮮期。消費者在選購相關食品時，可留意包裝上的成分標示，適量攝取即可。';

describe('splitSentences', () => {
  it('半角句點也算句尾——資料裡混用了', () => {
    expect(splitSentences(醋酸澱粉)).toHaveLength(3);
  });

  it('數字中間的小數點不切，否則限量數值會被裂成兩句', () => {
    const s = splitSentences('用量以NO2殘留量計為0.07 g/kg以下。生鮮肉類不得使用。');
    expect(s).toHaveLength(2);
    expect(s[0]).toContain('0.07');
  });

  it('空字串回空陣列，不回一個空句子', () => {
    expect(splitSentences('')).toEqual([]);
    expect(splitSentences('。。')).toEqual([]);
  });
});

describe('pickEssentialSentences', () => {
  it('丟掉定義句與用途句，留安全性評估與注意事項', () => {
    const picked = pickEssentialSentences(亞硝酸鈉);
    expect(picked).toHaveLength(2);
    // 第 1 句「是一種…保色劑與防腐劑」由類別徽章取代，不該出現在第一眼
    expect(picked.join('')).not.toContain('是一種常用於肉品加工');
    expect(picked[0]).toContain('合理限量下使用非常安全');
    expect(picked[1]).toContain('禁止使用於生鮮食材');
  });

  it('一句都沒命中時退回最後一句，不是第一句', () => {
    const picked = pickEssentialSentences(丙酸鈣);
    expect(picked).toHaveLength(1);
    expect(picked[0]).toContain('適量攝取即可');
    // 退回第一句就會拿到被徽章取代掉的定義，這是要防的
    expect(picked[0]).not.toContain('是一種常規烘焙食品防腐劑');
  });

  it('沒有可用內容時回空陣列，不編造替代文字', () => {
    expect(pickEssentialSentences('')).toEqual([]);
  });

  it('知識庫裡的殘留值照樣原樣回傳，由呼叫端決定要不要顯示', () => {
    // 正式庫裡真的有這筆（己二烯酸）。這一層不做內容判斷，
    // 但也不能因為它沒有句號就整個爆掉。
    expect(pickEssentialSentences('new description for testing admin approval'))
      .toEqual(['new description for testing admin approval']);
  });
});

describe('essentialDescription', () => {
  it('字數明顯比原文短', () => {
    for (const full of [亞硝酸鈉, 檸檬酸, 醋酸澱粉]) {
      const short = essentialDescription(full);
      expect(short.length).toBeLessThan(full.length);
      expect(short.endsWith('。')).toBe(true);
    }
  });

  it('沒內容時回空字串，讓呼叫端可以整塊不顯示', () => {
    expect(essentialDescription('')).toBe('');
  });
});

describe('remainingSentences', () => {
  it('第一眼沒顯示的句子都收在這裡，兩邊合起來不漏字', () => {
    const shown = pickEssentialSentences(檸檬酸);
    const rest = remainingSentences(檸檬酸);
    expect(shown.length + rest.length).toBe(splitSentences(檸檬酸).length);
    expect(rest.some(s => s.includes('天然存在於柑橘類水果'))).toBe(true);
  });

  it('全文都顯示過時回空陣列——不該出現點不出東西的「詳細說明」', () => {
    const 全命中 = '在合規限量下安全無虞。消費者不需擔心。';
    expect(remainingSentences(全命中)).toEqual([]);
  });
});
