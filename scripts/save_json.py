import json
import sys
import os

# Ensure stdout can output UTF-8 (Windows console default is CP1252/CP437)
if sys.stdout.encoding and not sys.stdout.encoding.lower().startswith("utf"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"

# Map English aliases to Japanese keys (avoids CLI encoding issues on Windows)
ALIAS_MAP = {
    "trainer.gender": ("characters", "\u30c8\u30ec\u30fc\u30ca\u30fc", "gender"),
}


def save_file(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")


def sort_characters(data):
    chars = list(data.get("characters", {}).items())
    trainer = [(jp, info) for jp, info in chars if info.get("name") == "Trainer"]
    characters = [(jp, info) for jp, info in chars if info.get("gender") not in ("Not Applicable", None) and info.get("name") != "Trainer"]
    races = [(jp, info) for jp, info in chars if info.get("gender") == "Not Applicable"]
    characters.sort(key=lambda x: x[1].get("name", "").lower())
    races.sort(key=lambda x: x[1].get("name", "").lower())
    data["characters"] = {}
    for jp, info in trainer + characters + races:
        data["characters"][jp] = info


def set_nested(data, key, val):
    parts = key.split(".") if isinstance(key, str) else key
    obj = data
    for part in parts[:-1]:
        obj = obj[part]
    obj[parts[-1]] = val


def parse_val(val):
    if val.isdigit():
        return int(val)
    try:
        return float(val)
    except ValueError:
        pass
    if val == "true":
        return True
    if val == "false":
        return False
    if val == "null":
        return None
    return val


if len(sys.argv) < 2:
    sys.exit("Usage: save_json.py <file> [--list|--add|--delete|--edit|--get] ...")

filepath = sys.argv[1]
args = sys.argv[2:]

# --- --list: output JSON array of [index, jp_name, name, gender, nickname, notes] ---
if "--list" in args:
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    chars = list(data.get("characters", {}).items())
    result = []
    for i, (jp_name, info) in enumerate(chars):
        result.append({
            "i": i,
            "jp": jp_name,
            "name": info.get("name", ""),
            "gender": info.get("gender", ""),
            "nickname": info.get("nickname"),
            "notes": info.get("notes")
        })
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.exit(0)

# --- --get <key>: read a value and print it ---
if "--get" in args:
    gi = args.index("--get")
    if gi + 1 < len(args):
        with open(filepath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        key = args[gi + 1]
        if key in ALIAS_MAP:
            parts = ALIAS_MAP[key]
        else:
            parts = key.split(".")
        val = data
        for part in parts:
            if isinstance(val, dict) and part in val:
                val = val[part]
            else:
                sys.exit("")
        if isinstance(val, str):
            sys.stdout.write(val)
        else:
            sys.stdout.write(json.dumps(val, ensure_ascii=False))
        sys.exit(0)

# --- --add <jp_name> <name> <gender> [--nickname val] [--notes val] [--notes-file path] ---
if "--add" in args:
    ai = args.index("--add")
    rest = args[ai + 1:]
    if len(rest) < 3:
        sys.exit("Usage: --add <jp_name> <name> <gender>")
    jp_name, name, gender = rest[0], rest[1], rest[2]
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    entry = {"name": name, "gender": gender}
    ni = rest.index("--nickname") if "--nickname" in rest else -1
    if ni >= 0 and ni + 1 < len(rest):
        nick_str = rest[ni + 1]
        if "," in nick_str:
            entry["nickname"] = [n.strip() for n in nick_str.split(",") if n.strip()]
        else:
            entry["nickname"] = nick_str
    notes_val = ""
    nfi = rest.index("--notes-file") if "--notes-file" in rest else -1
    if nfi >= 0 and nfi + 1 < len(rest):
        with open(rest[nfi + 1], "r", encoding="utf-8") as nf:
            notes_val = nf.read().strip()
    else:
        ni2 = rest.index("--notes") if "--notes" in rest else -1
        if ni2 >= 0 and ni2 + 1 < len(rest):
            notes_val = rest[ni2 + 1]
    if notes_val:
        entry["notes"] = notes_val
    data["characters"][jp_name] = entry
    sort_characters(data)
    save_file(filepath, data)
    sys.exit(0)

# --- --delete <index> ---
if "--delete" in args:
    di = args.index("--delete")
    if di + 1 < len(args):
        idx = int(args[di + 1])
        with open(filepath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        chars = list(data.get("characters", {}).items())
        if 0 <= idx < len(chars):
            jp_name = chars[idx][0]
            del data["characters"][jp_name]
            sort_characters(data)
            save_file(filepath, data)
        else:
            sys.exit("Index out of range")
    sys.exit(0)

# --- --edit <index> --fields <json_file> ---
if "--edit" in args:
    ei = args.index("--edit")
    if ei + 1 < len(args):
        idx = int(args[ei + 1])
        fields_file = None
        fi = args.index("--fields") if "--fields" in args else -1
        if fi >= 0 and fi + 1 < len(args):
            fields_file = args[fi + 1]
        with open(filepath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        chars = list(data.get("characters", {}).items())
        if 0 <= idx < len(chars):
            jp_name = chars[idx][0]
            if fields_file:
                with open(fields_file, "r", encoding="utf-8") as ff:
                    new_fields = json.load(ff)
                for k, v in new_fields.items():
                    if v is None:
                        data["characters"][jp_name].pop(k, None)
                    else:
                        data["characters"][jp_name][k] = v
            sort_characters(data)
            save_file(filepath, data)
        else:
            sys.exit("Index out of range")
    sys.exit(0)

 # --- --sort: sort characters alphabetically, races below ---
if "--sort" in args:
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    sort_characters(data)
    save_file(filepath, data)
    sys.exit(0)

# --- Default: read/write with key=value overrides (original behavior) ---
data = None
i = 0
while i < len(args):
    if args[i] == "-from" and i + 1 < len(args):
        with open(args[i + 1], "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        i += 2
    else:
        i += 1

if data is None:
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

# Apply key=value overrides
for arg in args:
    if "=" in arg and not arg.startswith("-"):
        key, val = arg.split("=", 1)
        if key in ALIAS_MAP:
            set_nested(data, ALIAS_MAP[key], parse_val(val))
        else:
            set_nested(data, key, parse_val(val))

save_file(filepath, data)
