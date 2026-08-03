import sys
import json
from pathlib import Path

def main():
    if len(sys.argv) < 3:
        print("Usage: python append_message_cmd.py <sender_cid> <content_json_string_or_filepath>")
        sys.exit(1)
        
    sender = sys.argv[1]
    content_arg = sys.argv[2]
    
    is_file = False
    if len(content_arg) < 255 and "{" not in content_arg:
        try:
            if Path(content_arg).exists():
                is_file = True
        except OSError:
            pass
            
    if is_file:
        with open(content_arg, "r", encoding="utf-8") as f:
            content_str = f.read().strip()
    else:
        content_str = content_arg.strip()
        
    try:
        data = json.loads(content_str)
        compact_content = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    except Exception as e:
        print(f"Error parsing content as JSON: {e}")
        sys.exit(1)
        
    filepath = "/home/laihome/projects/FoodScanIoT/添加物資料庫整理/04_工具腳本/system_messages.txt"
    entry = f"sender={sender} priority=MESSAGE_PRIORITY_HIGH content={compact_content}"
    
    if Path(filepath).exists():
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    else:
        lines = []
        
    prefix = f"sender={sender}"
    new_lines = []
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(entry)
            replaced = True
        else:
            new_lines.append(line)
            
    if not replaced:
        new_lines.append(entry)
        print(f"Appended new message from {sender}.")
    else:
        print(f"Replaced existing message from {sender}.")
        
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")

if __name__ == "__main__":
    main()
