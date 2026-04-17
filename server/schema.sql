-- 建立資料庫
CREATE DATABASE IF NOT EXISTS product_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE product_db;

-- 1. 產品主資料
CREATE TABLE IF NOT EXISTS products (
  barcode VARCHAR(20) PRIMARY KEY,
  name VARCHAR(200) NOT NULL,
  brand VARCHAR(100),
  manufacturer VARCHAR(100),
  origin VARCHAR(100),
  ingredients TEXT, -- 儲存 JSON array 字符串
  additives TEXT,   -- 儲存 JSON array 字符串
  nutrition TEXT,   -- 儲存 JSON object 字符串
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 2. 食品添加物知識庫
CREATE TABLE IF NOT EXISTS additives_knowledge (
  additive_id VARCHAR(50) PRIMARY KEY,
  name_zh VARCHAR(100) NOT NULL,
  name_en VARCHAR(100),
  synonyms TEXT, -- 儲存 JSON array: ["苯甲酸", "安息香酸"]
  purpose VARCHAR(200),
  risk_level ENUM('low', 'medium', 'high') DEFAULT 'low',
  risk_notes TEXT,
  sensitive_groups TEXT, -- 儲存 JSON array: ["pregnant", "child"]
  regulatory_limit TEXT
);

-- 3. 食安事件紀錄
CREATE TABLE IF NOT EXISTS food_safety_events (
  event_id INT AUTO_INCREMENT PRIMARY KEY,
  barcode VARCHAR(20),
  manufacturer VARCHAR(100),
  event_type ENUM('failed_inspection', 'recall', 'violation', 'warning') NOT NULL,
  severity ENUM('low', 'medium', 'high', 'critical') DEFAULT 'low',
  event_date DATE NOT NULL,
  summary TEXT,
  source_url VARCHAR(500),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_barcode (barcode),
  INDEX idx_mfg (manufacturer)
);

-- 種子資料: 添加物知識庫
INSERT INTO additives_knowledge (additive_id, name_zh, name_en, synonyms, purpose, risk_level, risk_notes, sensitive_groups)
VALUES 
('benzoate-sodium', '苯甲酸鈉', 'Sodium Benzoate', '["苯甲酸", "防腐劑", "安息香酸鈉"]', '防腐劑', 'high', '長期大量攝取可能影響肝功能，部分研究指出與過動症有關。', '["child", "pregnant"]'),
('nitrite-sodium', '亞硝酸鈉', 'Sodium Nitrite', '["亞硝酸鹽"]', '保色劑', 'high', '與胺類物質結合可能形成致癌物亞硝胺。', '["pregnant"]'),
('aspartame', '阿斯巴甜', 'Aspartame', '["代糖", "甜味劑"]', '甜味劑', 'medium', '苯酮尿症患者不宜食用，WHO 列為可能致癌物。', '["renal"]'),
('tartrazine', '食用黃色四號', 'Tartrazine', '["黃色4號", "E102"]', '著色劑', 'medium', '可能引起過敏反應，如蕁麻疹、氣喘。', '["child"]'),
('monosodium-glutamate', '味精', 'MSG', '["麩胺酸鈉", "L-麩酸鈉"]', '調味劑', 'low', '敏感族群可能出現頭痛、口乾等症狀。', '[]'),
('sorbic-acid', '山梨酸', 'Sorbic Acid', '["己二烯酸"]', '防腐劑', 'medium', '較安全的防腐劑，但過量仍具刺激性。', '[]'),
('xanthan-gum', '三仙膠', 'Xanthan Gum', '["黃原膠"]', '粘稠劑', 'low', '天然來源，安全性高。', '[]'),
('ascorbic-acid', '維生素C', 'Ascorbic Acid', '["抗壞血酸"]', '抗氧化劑', 'low', '營養補充，安全性極高。', '[]'),
('sunset-yellow', '食用黃色五號', 'Sunset Yellow', '["黃色5號", "E110"]', '著色劑', 'medium', '可能引起孩童過動。', '["child"]'),
('potassium-sorbate', '山梨酸鉀', 'Potassium Sorbate', '["己二烯酸鉀"]', '防腐劑', 'medium', '常見防腐劑，過量可能引起皮膚過敏。', '[]');
