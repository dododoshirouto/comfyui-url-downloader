"""URL の判定と変換（直リンク・GitHub・CivitAI）"""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class UrlError(ValueError):
    pass


_GITHUB_HOSTS = {"github.com", "www.github.com"}
_CIVITAI_HOSTS = {"civitai.com", "www.civitai.com"}


def _parse(url):
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise UrlError("http:// か https:// の URL を指定してください")
    return u


def is_civitai(url):
    return (urlparse(url).hostname or "").lower() in _CIVITAI_HOSTS


def civitai_api_url(url):
    """CivitAI の URL から、バージョン情報を取る API の URL を返す。直接ダウンロード URL なら None"""
    u = _parse(url)
    path = u.path.rstrip("/")
    if re.fullmatch(r"/api/download/models/\d+", path):
        return None
    m = re.fullmatch(r"/models/(\d+)(?:/[^/]*)?", path)
    if not m:
        raise UrlError("CivitAI のモデルページか、ダウンロード用の URL を指定してください")
    version = parse_qs(u.query).get("modelVersionId", [None])[0]
    if version:
        if not version.isdigit():
            raise UrlError("modelVersionId が不正です")
        return f"https://civitai.com/api/v1/model-versions/{version}"
    return f"https://civitai.com/api/v1/models/{m.group(1)}"


def civitai_pick_file(info):
    """API の応答から（ダウンロード URL, ファイル名）を返す。モデル情報なら最新バージョンを使う"""
    version = info
    if "modelVersions" in info:
        versions = info.get("modelVersions") or []
        if not versions:
            raise UrlError("CivitAI のモデルにバージョンがありません")
        version = versions[0]
    files = version.get("files") or []
    if not files:
        raise UrlError("CivitAI のバージョンにファイルがありません")
    f = next((x for x in files if x.get("primary")), files[0])
    if not f.get("downloadUrl"):
        raise UrlError("CivitAI のファイルにダウンロード URL がありません")
    return f["downloadUrl"], f.get("name")


def add_civitai_token(url, token):
    if not token:
        return url
    u = urlparse(url)
    q = parse_qs(u.query)
    q["token"] = [token]
    return urlunparse(u._replace(query=urlencode(q, doseq=True)))


def github_model_url(url):
    """GitHub のファイル URL をダウンロードできる URL にする"""
    u = _parse(url)
    host = (u.hostname or "").lower()
    if host == "raw.githubusercontent.com":
        return url.strip()
    if host not in _GITHUB_HOSTS:
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 5 and parts[2] == "blob":
        parts[2] = "raw"
        return urlunparse(u._replace(netloc="github.com", path="/" + "/".join(parts), query="", fragment=""))
    if len(parts) >= 5 and parts[2] == "raw":
        return url.strip()
    if len(parts) >= 6 and parts[2] == "releases" and parts[3] == "download":
        return url.strip()
    raise UrlError("GitHub のファイルの URL ではありません（blob・raw・releases/download の URL を指定してください）")


def model_download_url(url):
    """モデル用：（ダウンロード URL, CivitAI の API URL または None）"""
    u = _parse(url)
    if is_civitai(url):
        return url.strip(), civitai_api_url(url)
    gh = github_model_url(url)
    if gh:
        return gh, None
    return urlunparse(u._replace(fragment="")), None


def github_repo(url):
    """カスタムノード用：（clone する URL, リポジトリ名, ブランチまたは None）"""
    u = _parse(url)
    if (u.hostname or "").lower() not in _GITHUB_HOSTS:
        raise UrlError("カスタムノードは GitHub のリポジトリ URL を指定してください")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        raise UrlError("GitHub のリポジトリ URL を指定してください（https://github.com/<owner>/<repo>）")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    name_ok = re.compile(r"^[A-Za-z0-9._-]+$")
    if not name_ok.match(owner) or not name_ok.match(repo) or repo in (".", ".."):
        raise UrlError("GitHub のリポジトリ URL が不正です")
    branch = None
    if len(parts) >= 4 and parts[2] == "tree":
        branch = "/".join(parts[3:])
        if not re.match(r"^[A-Za-z0-9._/-]+$", branch) or branch.startswith("-"):
            raise UrlError("ブランチ名が不正です")
    elif len(parts) > 2:
        raise UrlError("GitHub のリポジトリ URL を指定してください（https://github.com/<owner>/<repo>）")
    return f"https://github.com/{owner}/{repo}.git", repo, branch
