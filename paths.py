"""保存先の決定と検証"""

import os
import re

EXCLUDED_MODEL_TYPES = {"custom_nodes", "configs"}
_BAD_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class PathError(ValueError):
    pass


def _folder_paths():
    import folder_paths  # ComfyUI 本体のモジュール

    return folder_paths


def model_types():
    """モデルの種類と保存先（先頭のパス）の一覧"""
    fp = _folder_paths()
    result = []
    for name in sorted(fp.folder_names_and_paths):
        if name in EXCLUDED_MODEL_TYPES:
            continue
        paths = fp.get_folder_paths(name)
        if paths:
            result.append({"name": name, "path": paths[0]})
    return result


PERSISTENT_ROOT = "/runpod-volume"


def custom_nodes_dir():
    """カスタムノードの保存先。custom_nodes_dev があればそこ、無ければ探索パスの先頭（Manager のインストール先と同じ）"""
    candidates = _folder_paths().get_folder_paths("custom_nodes")
    for p in candidates:
        if "custom_nodes_dev" in p.replace("\\", "/").split("/"):
            return p
    return candidates[0] if candidates else None


def is_persistent(path):
    """Network Volume 上か（外ならワーカーの入れ替わりで消える）"""
    p = path.replace("\\", "/")
    return p == PERSISTENT_ROOT or p.startswith(PERSISTENT_ROOT + "/")


def list_models():
    """種類ごとに、登録されているすべてのフォルダ配下のファイルとサイズを返す（.part・隠しファイルは除く）"""
    fp = _folder_paths()
    result = []
    for name in sorted(fp.folder_names_and_paths):
        if name in EXCLUDED_MODEL_TYPES:
            continue
        files = []
        for base in fp.get_folder_paths(name):
            if not os.path.isdir(base):
                continue
            for root, dirs, names in os.walk(base, followlinks=True):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for n in sorted(names):
                    if n.startswith(".") or n.endswith(".part"):
                        continue
                    full = os.path.join(root, n)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        continue
                    files.append({
                        "path": full,
                        "relpath": os.path.relpath(full, base).replace("\\", "/"),
                        "base": base,
                        "size": size,
                        "persistent": is_persistent(base),
                    })
        if files:
            result.append({"name": name, "files": files, "total": sum(f["size"] for f in files)})
    return result


def delete_model(model_type, path):
    """登録済みフォルダ配下のファイルだけを消す。ComfyUI のファイル一覧キャッシュも捨てる"""
    if model_type in EXCLUDED_MODEL_TYPES:
        raise PathError(f"モデルの種類が不正です: {model_type}")
    fp = _folder_paths()
    if model_type not in fp.folder_names_and_paths:
        raise PathError(f"モデルの種類が不正です: {model_type}")
    target = os.path.realpath(path or "")
    for base in fp.get_folder_paths(model_type):
        base_real = os.path.realpath(base)
        if os.path.commonpath([base_real, target]) == base_real and target != base_real:
            break
    else:
        raise PathError("削除できるのは、このモデルの種類のフォルダ内のファイルだけです")
    if not os.path.isfile(target):
        raise PathError(f"ファイルがありません: {path}")
    os.remove(target)
    cache = getattr(fp, "filename_list_cache", None)
    if isinstance(cache, dict):
        cache.pop(model_type, None)
    return target


def model_base_dir(model_type):
    if model_type in EXCLUDED_MODEL_TYPES:
        raise PathError(f"モデルの種類が不正です: {model_type}")
    fp = _folder_paths()
    if model_type not in fp.folder_names_and_paths:
        raise PathError(f"モデルの種類が不正です: {model_type}")
    paths = fp.get_folder_paths(model_type)
    if not paths:
        raise PathError(f"モデルの種類 {model_type} に保存先がありません")
    return paths[0]


def split_subfolder(subfolder):
    """サブフォルダを要素に分ける。空・.・..・絶対パス・制御文字を含む場合はエラー"""
    if not subfolder:
        return []
    s = subfolder.strip()
    if not s:
        return []
    if s.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", s):
        raise PathError("保存先が不正です（絶対パスは指定できません）")
    parts = re.split(r"[\\/]", s)
    for part in parts:
        if part in ("", ".", "..") or _BAD_CHARS.search(part) or part != part.strip():
            raise PathError(f"保存先が不正です: {subfolder}")
    return parts


def validate_filename(filename):
    if not filename or filename in (".", "..") or "/" in filename or "\\" in filename or ".." in filename:
        raise PathError(f"ファイル名が不正です: {filename}")
    if _BAD_CHARS.search(filename) or filename != filename.strip():
        raise PathError(f"ファイル名が不正です: {filename}")
    return filename


def ensure_inside(base, target):
    base_real = os.path.realpath(base)
    target_real = os.path.realpath(target)
    if os.path.commonpath([base_real, target_real]) != base_real or target_real == base_real:
        raise PathError("保存先が不正です（保存先フォルダの外になります）")
    return target_real


def model_dest_dir(model_type, subfolder):
    base = model_base_dir(model_type)
    parts = split_subfolder(subfolder)
    if not parts:
        return base
    return ensure_inside(base, os.path.join(base, *parts))


def model_dest_file(model_type, subfolder, filename):
    d = model_dest_dir(model_type, subfolder)
    return ensure_inside(model_base_dir(model_type), os.path.join(d, validate_filename(filename)))
