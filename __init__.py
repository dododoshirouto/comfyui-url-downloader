# URL からモデル・カスタムノードをダウンロードする拡張（ノードは登録しない）
from aiohttp import web
from server import PromptServer

from . import downloader, paths

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

routes = PromptServer.instance.routes


@routes.get("/url_downloader/model_types")
async def get_model_types(request):
    return web.json_response({"model_types": paths.model_types(), "custom_nodes_dir": paths.custom_nodes_dir()})


@routes.get("/url_downloader/jobs")
async def get_jobs(request):
    return web.json_response({"jobs": downloader.list_jobs()})


@routes.post("/url_downloader/jobs")
async def post_job(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "JSON の本文を送ってください"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON の本文を送ってください"}, status=400)
    try:
        job = downloader.validate_request(body)
    except downloader.JobError as e:
        return web.json_response({"error": str(e)}, status=400)
    downloader.submit(job)
    return web.json_response({"job": job.to_dict()})


@routes.post("/url_downloader/jobs/{id}/cancel")
async def cancel_job(request):
    if not downloader.cancel(request.match_info["id"]):
        return web.json_response({"error": "ジョブがありません"}, status=404)
    return web.json_response({"ok": True})


@routes.delete("/url_downloader/jobs/{id}")
async def delete_job(request):
    result = downloader.remove(request.match_info["id"])
    if result is None:
        return web.json_response({"error": "ジョブがありません"}, status=404)
    if result is False:
        return web.json_response({"error": "実行中のジョブは消せません。先に中止してください"}, status=409)
    return web.json_response({"ok": True})
