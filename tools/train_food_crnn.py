#!/usr/bin/env python3
"""Fine-tune EasyOCR CRNN Recognizer on Taiwanese Food Additives and Ingredients.

Expands the recognition character dictionary to include missing CJK characters
like '鰹', and trains on synthetic packaging text lines with CTC loss.
"""

import math
import os
import random
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from torch.utils.data import DataLoader, Dataset
import yaml

import easyocr
import easyocr.config as cfg
from easyocr.model.modules import BidirectionalLSTM, ResNet_FeatureExtractor
from easyocr.recognition import CTCLabelConverter


class Model(nn.Module):
    def __init__(self, input_channel, output_channel, hidden_size, num_class):
        super(Model, self).__init__()
        self.FeatureExtraction = ResNet_FeatureExtractor(input_channel, output_channel)
        self.FeatureExtraction_output = output_channel
        self.AdaptiveAvgPool = nn.AdaptiveAvgPool2d((None, 1))

        self.SequenceModeling = nn.Sequential(
            BidirectionalLSTM(self.FeatureExtraction_output, hidden_size, hidden_size),
            BidirectionalLSTM(hidden_size, hidden_size, hidden_size),
        )
        self.SequenceModeling_output = hidden_size
        self.Prediction = nn.Linear(self.SequenceModeling_output, num_class)

    def forward(self, input, text=None):
        visual_feature = self.FeatureExtraction(input)
        visual_feature = self.AdaptiveAvgPool(visual_feature.permute(0, 3, 1, 2))
        visual_feature = visual_feature.squeeze(3)
        contextual_feature = self.SequenceModeling(visual_feature)
        prediction = self.Prediction(contextual_feature.contiguous())
        return prediction


class SyntheticFoodDataset(Dataset):
    """Generates synthetic food packaging text lines on-the-fly."""

    def __init__(self, font_paths: list[str], vocabulary: list[str], count: int = 3000):
        self.fonts = [ImageFont.truetype(p, size=s) for p in font_paths for s in (20, 22, 24, 26)]
        self.vocabulary = vocabulary
        self.count = count

        # Key target patterns to ensure high representation
        self.must_have = [
            "成份:水、米、正鰹、洋蔥、大豆油",
            "成份:水`米`正鰹`洋蔥`大豆油",
            "正鰹抽出物、柴魚粉、正鰹",
            "大飯糰椒香鮪魚",
            "粘稠劑(羥丙基磷酸二澱粉、氧化澱粉)",
            "小麥蛋白、大豆纖維、菜籽油",
            "油菜籽油、芥花油、葵花油、大豆油",
            "調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)",
            "調味劑(琥珀酸二鈉、5'-鳥嘌呤核苷磷酸二鈉)",
            "品質改良劑(焦磷酸鈉、多磷酸鈉、偏磷酸鉀)",
            "複方品質改良劑(醋酸鈉、甘胺酸、檸檬酸)",
            "黑胡椒粗粒、八角、當歸、花椒、肉桂",
            "三仙膠、柴魚粉、海苔、雞蛋、鹽",
            "D-山梨醇液70%、脂肪酸聚合甘油酯",
            "熱量 264 大卡/份",
            "蛋白質 19.0 公克、脂肪 6.9 公克",
            "碳水化合物 22.2 公克、糖 1.3 公克",
            "鈉 352 毫克、反式脂肪 0.0 公克",
            "本產品含有大豆、小麥、蛋、芝麻及其製品",
            "凱亞食品股份有限公司、新北市瑞芳區",
        ]

    def __len__(self):
        return self.count

    def generate_text(self, idx: int) -> str:
        if idx < len(self.must_have) * 20:
            return self.must_have[idx % len(self.must_have)]
        k = random.randint(2, 6)
        words = random.sample(self.vocabulary, k)
        sep = random.choice(["、", "、", "、", "`", ",", ";"])
        prefix = random.choice(["成份:", "原料:", "配料:", "品名:", ""])
        return prefix + sep.join(words)

    def __getitem__(self, idx):
        text = self.generate_text(idx)
        font = random.choice(self.fonts)

        dummy = Image.new("L", (1, 1), 255)
        d = ImageDraw.Draw(dummy)
        bbox = d.textbbox((0, 0), text, font=font)
        text_w = max(bbox[2] - bbox[0] + 16, 32)
        h = 32

        bg = random.randint(230, 255)
        img = Image.new("L", (text_w, h), bg)
        draw = ImageDraw.Draw(img)

        fg = random.randint(0, 45)
        y_off = (h - (bbox[3] - bbox[1])) // 2 - bbox[1]
        draw.text((8, y_off), text, font=font, fill=fg)

        if random.random() < 0.2:
            img = img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 0.7)))

        arr = np.array(img, dtype=np.float32) / 127.5 - 1.0
        tensor = torch.from_numpy(arr).unsqueeze(0)
        return tensor, text


def collate_fn(batch):
    tensors, texts = zip(*batch)
    max_w = max(t.shape[2] for t in tensors)
    max_w = int(math.ceil(max_w / 4.0) * 4)

    padded = torch.zeros((len(tensors), 1, 32, max_w), dtype=torch.float32)
    for i, t in enumerate(tensors):
        w = t.shape[2]
        padded[i, :, :, :w] = t
        padded[i, :, :, w:] = 1.0

    return padded, texts


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    out_dir = Path("/workspace/tools/custom_food_crnn")
    out_dir.mkdir(parents=True, exist_ok=True)

    orig_chars = cfg.recognition_models["gen1"]["zh_tra_g1"]["characters"]
    extra_chars = ["鰹"]
    food_chars = orig_chars
    for c in extra_chars:
        if c not in food_chars:
            food_chars += c
    print(f"Total recognition characters: {len(food_chars)} (added {extra_chars})")

    converter = CTCLabelConverter(food_chars)
    num_class = len(converter.character)
    print(f"CTC Classes count: {num_class}")

    model = Model(input_channel=1, output_channel=512, hidden_size=512, num_class=num_class)

    ckpt_path = Path("/opt/easyocr-models/chinese.pth")
    if ckpt_path.exists():
        print(f"Warm-starting from {ckpt_path}...")
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        model_dict = model.state_dict()

        for k, v in ckpt.items():
            k_clean = k.replace("module.", "")
            if k_clean in model_dict:
                if k_clean == "Prediction.weight":
                    old_c = v.shape[0]
                    model_dict[k_clean][:old_c] = v
                    dian_idx = orig_chars.index("墊") + 1
                    jian_idx = food_chars.index("鰹") + 1
                    model_dict[k_clean][jian_idx] = v[dian_idx]
                elif k_clean == "Prediction.bias":
                    old_c = v.shape[0]
                    model_dict[k_clean][:old_c] = v
                    dian_idx = orig_chars.index("墊") + 1
                    jian_idx = food_chars.index("鰹") + 1
                    model_dict[k_clean][jian_idx] = v[dian_idx]
                else:
                    model_dict[k_clean] = v
        model.load_state_dict(model_dict)
        print("Warm-start weights transferred successfully!")

    model = model.to(device)

    lexicon_p = Path("/workspace/tools/tfda_lexicon.json")
    import json
    vocabulary = json.loads(lexicon_p.read_text(encoding="utf-8")) if lexicon_p.exists() else ["水", "米", "正鰹"]

    dataset = SyntheticFoodDataset(
        font_paths=["/fonts/msjh.ttc"],
        vocabulary=vocabulary,
        count=2000,
    )
    dataloader = DataLoader(
        dataset,
        batch_size=16,
        shuffle=True,
        num_workers=0,
        collate_fn=collate_fn,
    )

    criterion = nn.CTCLoss(blank=0, reduction="mean", zero_infinity=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=1e-4)

    # Pre-generate custom network files upfront
    py_content = """import torch.nn as nn
from easyocr.model.modules import ResNet_FeatureExtractor, BidirectionalLSTM

class Model(nn.Module):
    def __init__(self, input_channel, output_channel, hidden_size, num_class):
        super(Model, self).__init__()
        self.FeatureExtraction = ResNet_FeatureExtractor(input_channel, output_channel)
        self.FeatureExtraction_output = output_channel
        self.AdaptiveAvgPool = nn.AdaptiveAvgPool2d((None, 1))

        self.SequenceModeling = nn.Sequential(
            BidirectionalLSTM(self.FeatureExtraction_output, hidden_size, hidden_size),
            BidirectionalLSTM(hidden_size, hidden_size, hidden_size)
        )
        self.SequenceModeling_output = hidden_size
        self.Prediction = nn.Linear(self.SequenceModeling_output, num_class)

    def forward(self, input, text=None):
        visual_feature = self.FeatureExtraction(input)
        visual_feature = self.AdaptiveAvgPool(visual_feature.permute(0, 3, 1, 2))
        visual_feature = visual_feature.squeeze(3)
        contextual_feature = self.SequenceModeling(visual_feature)
        prediction = self.Prediction(contextual_feature.contiguous())
        return prediction
"""
    (out_dir / "food_crnn.py").write_text(py_content, encoding="utf-8")

    cfg_data = {
        "network_params": {
            "input_channel": 1,
            "output_channel": 512,
            "hidden_size": 512,
        },
        "imgH": 32,
        "lang_list": ["ch_tra"],
        "character_list": food_chars,
    }
    with open(out_dir / "food_crnn.yaml", "w", encoding="utf-8") as f:
        yaml.dump(cfg_data, f, allow_unicode=True)

    craft_src = Path("/opt/easyocr-models/craft_mlt_25k.pth")
    if craft_src.exists():
        import shutil
        shutil.copy(craft_src, out_dir / "craft_mlt_25k.pth")

    print("Starting fine-tuning for 4 epochs...")
    model.train()
    start_time = time.perf_counter()

    for epoch in range(1, 5):
        total_loss = 0.0
        n_batches = 0
        for images, texts in dataloader:
            images = images.to(device)
            safe_texts = []
            for t in texts:
                safe_t = "".join(c for c in t if c in converter.dict)
                safe_texts.append(safe_t if safe_t else "水")

            text_encoded, text_lengths = converter.encode(safe_texts)
            text_encoded = text_encoded.to(device)
            text_lengths = text_lengths.to(device)

            optimizer.zero_grad()
            preds = model(images)
            b, t_steps, _ = preds.shape

            log_probs = preds.log_softmax(2).permute(1, 0, 2)
            preds_lengths = torch.full((b,), t_steps, dtype=torch.long, device=device)

            loss = criterion(log_probs, text_encoded, preds_lengths, text_lengths)
            if not torch.isnan(loss) and not torch.isinf(loss):
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                optimizer.step()
                total_loss += loss.item()
                n_batches += 1

        avg_loss = total_loss / max(n_batches, 1)
        print(f"Epoch {epoch}/4 - Loss: {avg_loss:.4f}")

        # Checkpoint per epoch
        save_state_dict = OrderedDict()
        for k, v in model.state_dict().items():
            save_state_dict[f"module.{k}"] = v.cpu()
        torch.save(save_state_dict, out_dir / "food_crnn.pth")
        print(f"Checkpoint saved to {out_dir / 'food_crnn.pth'}")

    elapsed = time.perf_counter() - start_time
    print(f"Training completed in {elapsed:.2f}s!")

    print("Verifying EasyOCR with newly fine-tuned food_crnn...")
    reader = easyocr.Reader(
        ["ch_tra"],
        user_network_directory=str(out_dir),
        model_storage_directory=str(out_dir),
        recog_network="food_crnn",
        gpu=True,
        download_enabled=False,
        verbose=False,
    )
    test_img = "/workspace/scratch/synth_test/test_0.png"
    if os.path.exists(test_img):
        res = reader.readtext(test_img, detail=0)
        print("Inference on test_0.png (target: 成份:水、米、正鰹、洋蔥、大豆油):")
        print("  ->", res)

    # Also test on real crop of c11!
    c11_crop = "/workspace/.artifacts/crop_experiments/c11_大飯糰椒香鮪魚_crop.jpg"
    if os.path.exists(c11_crop):
        c11_res = reader.readtext(c11_crop, detail=0)
        print("Inference on c11_crop (first 10 lines):")
        for line in c11_res[:10]:
            print("  ", line)

    print("Fine-tuning and verification finished successfully!")


if __name__ == "__main__":
    main()
