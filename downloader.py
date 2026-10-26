"""ジョブ管理・ダウンロード・git clone"""

import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from . import paths, url_resolver

log = logging.getLogger("url_downloader")

CHUNK = 1024 * 1024
MAX_REDIRECTS = 10
USER_AGENT = "comfyui-url-downloader/1.0"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "downloading"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"
ACTIVE = {STATUS_QUEUED, STATUS_RUNNING}


class JobError(Exception):
    pass


class Job:
    def __init__(self, kind, url, model_type=None, subfolder="", filename="", civitai_token="", hf_token=""):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.url = url
        self.model_type = model_type
        self.subfolder = subfolder or ""
        self.filename = filename or ""
        # トークンは一覧に出さない
        self._civitai_token = civitai_token or ""
        self._hf_token = hf_token or ""
        self.status = STATUS_QUEUED
        self.dest = None
        self.received = 0
        self.total = None
        self.speed = 0.0
        self.message = ""
        self.warning = ""
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.task = None

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "url": self.url,
            "status": self.status,
            "dest": self.dest,
            "received": self.received,
            "total": self.total,
            "speed": round(self.speed),
            "message": self.message,
            "warning": self.warning,
            "created_at": self.created_at,
        }


_jobs = {}
# 同時に 1 件ずつ処理する（ディスクと回線の取り合いを避ける）
_slot = asyncio.Semaphore(1)


def list_jobs():
    return [j.to_dict() for j in sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)]


def validate_request(body):
    """入力を検証して Job を作る（不正なら JobError）。この時点で分かる保存先も確かめる"""
    kind = body.get("kind")
    url = (body.get("url") or "").strip()
    try:
        if kind == "model":
            model_type = body.get("model_type") or ""
            url_resolver.model_download_url(url)
            dest_dir = paths.model_dest_dir(model_type, body.get("subfolder") or "")
            filename = (body.get("filename") or "").strip()
            if filename:
                dest = paths.model_dest_file(model_type, body.get("subfolder") or "", filename)
                if os.path.exists(dest):
                    raise JobError(f"既に存在します: {dest}")
            job = Job(kind, url, model_type, body.get("subfolder"), filename,
                      body.get("civitai_token"), body.get("hf_token"))
            job.dest = dest if filename else dest_dir
            return job
        if kind == "custom_node":
            _, repo, _ = url_resolver.github_repo(url)
            base = paths.custom_nodes_dir()
            if not base:
                raise JobError("custom_nodes_dev が見つかりません（extra_model_paths.yaml の custom_nodes を確認してください）")
            dest = os.path.join(base, repo)
            if os.path.exists(dest):
                raise JobError(f"既に存在します: {dest}")
            job = Job(kind, url)
            job.dest = dest
            return job
    except (paths.PathError, url_resolver.UrlError) as e:
        raise JobError(str(e)) from e
    raise JobError("kind は model か custom_node を指定してください")


def submit(job):
    _jobs[job.id] = job
    job.task = asyncio.get_running_loop().create_task(_run(job))
    return job


def cancel(job_id):
    job = _jobs.get(job_id)
    if not job:
        return False
    if job.status in ACTIVE and job.task:
        job.task.cancel()
    return True


def remove(job_id):
    job = _jobs.get(job_id)
    if not job:
        return None
    if job.status in ACTIVE:
        return False
    del _jobs[job_id]
    return True


async def _run(job):
    try:
        async with _slot:
            job.status = STATUS_RUNNING
            if job.kind == "model":
                await _download_model(job)
                job.message = "完了しました。モデル一覧を更新するには、ノードのモデル選択欄を開き直すか、画面を再読み込みしてください"
            else:
                await _clone_custom_node(job)
                job.message = "完了しました。ワーカーを再起動すると読み込まれます"
            job.status = STATUS_DONE
    except asyncio.CancelledError:
        job.status = STATUS_CANCELED
        job.message = "中止しました"
    except (JobError, paths.PathError, url_resolver.UrlError) as e:
        job.status = STATUS_FAILED
        job.message = str(e)
    except Exception as e:  # 想定外のエラーでもサーバは止めない
        log.exception("url_downloader: job %s failed", job.id)
        job.status = STATUS_FAILED
        job.message = f"エラー: {e}"
    finally:
        job.speed = 0.0


# ---- モデル ----

def _filename_from_disposition(value):
    if not value:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", value, re.I)
    if m:
        return unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', value, re.I) or re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    return m.group(1).strip() if m else None


async def _open_with_redirects(session, url, auth_host, auth_header):
    """リダイレクトを自前で追う。認証ヘッダは最初のホストにだけ付ける"""
    for _ in range(MAX_REDIRECTS + 1):
        host = (urlparse(url).hostname or "").lower()
        headers = {"User-Agent": USER_AGENT}
        if auth_header and host == auth_host:
            headers["Authorization"] = auth_header
        resp = await session.get(url, headers=headers, allow_redirects=False)
        if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
            url = urljoin(url, resp.headers["Location"])
            resp.release()
            continue
        return resp, url
    raise JobError("リダイレクトが多すぎます")


async def _download_model(job):
    download_url, api_url = url_resolver.model_download_url(job.url)
    civitai = url_resolver.is_civitai(job.url)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        suggested = None
        if api_url:
            async with session.get(api_url, headers={"User-Agent": USER_AGENT}) as r:
                if r.status != 200:
                    raise JobError(f"CivitAI のモデル情報を取得できません（{r.status}）")
                download_url, suggested = url_resolver.civitai_pick_file(await r.json())
        if civitai:
            download_url = url_resolver.add_civitai_token(download_url, job._civitai_token)

        start_host = (urlparse(download_url).hostname or "").lower()
        auth = f"Bearer {job._hf_token}" if job._hf_token and start_host == "huggingface.co" else None
        resp, final_url = await _open_with_redirects(session, download_url, start_host, auth)
        async with resp:
            if resp.status in (401, 403):
                if civitai:
                    raise JobError("CivitAI のトークンが必要です（設定画面で CivitAI の API トークンを入れてください）")
                if start_host == "huggingface.co":
                    raise JobError("Hugging Face のトークンが必要です（設定画面で Hugging Face のトークンを入れてください）")
                raise JobError(f"ダウンロードを拒否されました（{resp.status}）")
            if resp.status != 200:
                raise JobError(f"ダウンロードに失敗しました（{resp.status}）")

            filename = (job.filename or suggested
                        or _filename_from_disposition(resp.headers.get("Content-Disposition"))
                        or unquote(os.path.basename(urlparse(final_url).path)))
            if not filename:
                raise JobError("ファイル名を決められません。ファイル名を入力してください")
            dest = paths.model_dest_file(job.model_type, job.subfolder, filename)
            job.dest = dest
            if os.path.exists(dest):
                raise JobError(f"既に存在します: {dest}")

            total = resp.content_length
            job.total = total
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if total:
                free = shutil.disk_usage(os.path.dirname(dest)).free
                if free < total:
                    raise JobError(f"空き容量が足りません（必要 {total} バイト、空き {free} バイト）")

            part = dest + ".part"
            try:
                await _stream_to_file(resp, part, job)
                if os.path.exists(dest):
                    raise JobError(f"既に存在します: {dest}")
                os.replace(part, dest)
            finally:
                if os.path.exists(part):
                    os.remove(part)


async def _stream_to_file(resp, path, job):
    window_start, window_bytes = time.monotonic(), 0
    with open(path, "wb") as f:
        async for chunk in resp.content.iter_chunked(CHUNK):
            f.write(chunk)
            job.received += len(chunk)
            window_bytes += len(chunk)
            elapsed = time.monotonic() - window_start
            if elapsed >= 1.0:
                job.speed = window_bytes / elapsed
                window_start, window_bytes = time.monotonic(), 0


# ---- カスタムノード ----

async def _clone_custom_node(job):
    clone_url, _, branch = url_resolver.github_repo(job.url)
    dest = job.dest
    if os.path.exists(dest):
        raise JobError(f"既に存在します: {dest}")
    args = ["git", "clone", "--depth", "1"]
    if branch:
        args += ["--branch", branch]
    args += ["--", clone_url, dest]
    # 非公開リポジトリなどで認証を求められても止まらないようにする
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    try:
        _, stderr = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise JobError("git clone に失敗しました: " + stderr.decode(errors="replace").strip()[-500:])
    if os.path.exists(os.path.join(dest, "requirements.txt")):
        job.warning = "requirements.txt があります。追加のパッケージは自動では入りません。必要ならイメージに焼き込んでください"
