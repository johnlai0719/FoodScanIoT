import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DIR = PROJECT_ROOT / "添加物資料庫整理" / "04_工具腳本"

def main():
    with open(DIR / "subagent_calls.json", "r", encoding="utf-8") as f:
        calls = json.load(f)
        
    # Batch 1: ADD-0001 and ADD-0002 (tasks 0-5)
    batch1 = calls[0:6]
    # Batch 2: ADD-0003 and ADD-0004 (tasks 6-10)
    batch2 = calls[6:11]
    # Batch 3: ADD-0005 (tasks 11-12)
    batch3 = calls[11:13]
    
    with open(DIR / "calls_batch1.json", "w", encoding="utf-8") as f:
        json.dump(batch1, f, ensure_ascii=False, indent=2)
        
    with open(DIR / "calls_batch2.json", "w", encoding="utf-8") as f:
        json.dump(batch2, f, ensure_ascii=False, indent=2)
        
    with open(DIR / "calls_batch3.json", "w", encoding="utf-8") as f:
        json.dump(batch3, f, ensure_ascii=False, indent=2)
        
    print("Successfully split subagent calls into 3 batch files!")

if __name__ == "__main__":
    main()
