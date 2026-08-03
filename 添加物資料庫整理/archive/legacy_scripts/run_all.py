import subprocess
import sys
from pathlib import Path

# Configure encoding for stdout
sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent

def run_command(args):
    result = subprocess.run([sys.executable] + args, capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT_DIR))
    return result

def main():
    loop = 1
    while True:
        print(f"\n==================== LOOP {loop} ====================")
        # 1. Prepare
        print("Running prepare...")
        prep_res = run_command(["04_工具腳本/agent_batch_coordinator.py", "prepare", "--size", "30"])
        print(prep_res.stdout)
        if prep_res.stderr:
            print("Error in prepare:", prep_res.stderr)
            
        if "All records have been synthesized!" in prep_res.stdout or "No batch to prepare" in prep_res.stdout:
            print("🎉 All records completed!")
            break
            
        # 2. Synthesis
        print("Running local synthesis...")
        synth_res = run_command(["04_工具腳本/local_synthesis.py"])
        print(synth_res.stdout)
        if synth_res.stderr:
            print("Error in synthesis:", synth_res.stderr)
            sys.exit(1)
            
        # 3. Merge
        print("Running merge...")
        merge_res = run_command(["04_工具腳本/agent_batch_coordinator.py", "merge"])
        print(merge_res.stdout)
        if "Validation failed" in merge_res.stdout or "Error" in merge_res.stdout or merge_res.returncode != 0:
            print("❌ Merge failed!")
            print(merge_res.stderr)
            sys.exit(1)
            
        loop += 1

if __name__ == "__main__":
    main()
