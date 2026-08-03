import json
from pathlib import Path

def main():
    with open("subagents_to_run.json", "r", encoding="utf-8") as f:
        subagents = json.load(f)
        
    chunk_size = 35
    chunks = [subagents[i:i + chunk_size] for i in range(0, len(subagents), chunk_size)]
    
    # Create directory for batches
    batches_dir = Path("batches")
    batches_dir.mkdir(exist_ok=True)
    
    # Remove existing files in batches directory
    for f in batches_dir.glob("*.json"):
        f.unlink()
        
    for idx, chunk in enumerate(chunks):
        out_path = batches_dir / f"batch_{idx}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)
            
    print(f"Split {len(subagents)} subagents into {len(chunks)} batch files under 'batches/' directory.")

if __name__ == "__main__":
    main()
