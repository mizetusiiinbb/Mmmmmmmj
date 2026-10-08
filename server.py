import os
import io
import json
import secrets
import zipfile
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import httpx

from fastapi.middleware.cors import CORSMiddleware
from obfuscator import (
    generate_worker_name, generate_admin_path,
    generate_uuid, generate_d1_name,
    obfuscate_worker_js, generate_wrangler_toml, generate_readme
)

app = FastAPI(title="Zeus Panel Deployer", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

ZEUS_GITHUB_RAW = "https://raw.githubusercontent.com/panel-zeus/Z-E-U-S/refs/heads/main/Source.js"


class CloudflareDeployRequest(BaseModel):
    cf_token: str
    worker_name: Optional[str] = None
    admin_path: Optional[str] = None
    uuid: Optional[str] = None


class DownloadRequest(BaseModel):
    worker_name: Optional[str] = None
    admin_path: Optional[str] = None
    uuid: Optional[str] = None


async def fetch_zeus_source() -> str:
    """Fetch latest Zeus worker source from GitHub."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(ZEUS_GITHUB_RAW)
        if resp.status_code == 200:
            return resp.text
        raise HTTPException(
            status_code=502,
            detail="خطا در دریافت سورس کد زئوس از GitHub. لطفاً دوباره تلاش کنید."
        )


def build_config(req_name=None, req_path=None, req_uuid=None):
    return {
        "worker_name": req_name or generate_worker_name(),
        "admin_path": req_path or generate_admin_path(),
        "uuid": req_uuid or generate_uuid(),
        "d1_name": generate_d1_name(),
        "salt": secrets.token_hex(16),
    }


class ProxyRequest(BaseModel):
    token: str
    method: str
    path: str
    body: Optional[dict] = None


@app.post("/proxy")
async def cf_proxy(req: ProxyRequest):
    """Proxy Cloudflare API calls to avoid CORS."""
    url = f"https://api.cloudflare.com/client/v4{req.path}"
    headers = {"Authorization": f"Bearer {req.token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if req.method.upper() == "GET":
            r = await client.get(url, headers=headers)
        elif req.method.upper() == "POST":
            r = await client.post(url, headers=headers, json=req.body or {})
        elif req.method.upper() == "PUT":
            r = await client.put(url, headers=headers, json=req.body or {})
        else:
            raise HTTPException(status_code=400, detail="Method not supported")
    return r.json()


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = os.path.join(TEMPLATES_DIR, "index.html")
    with open(index_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/api/deploy/cloudflare")
async def deploy_cloudflare(req: CloudflareDeployRequest):
    token = req.cf_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="توکن Cloudflare الزامی است.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Verify token
        verify = await client.get(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            headers=headers
        )
        if verify.status_code != 200:
            raise HTTPException(status_code=401, detail="توکن Cloudflare نامعتبر است.")

        # 2. Get Account ID
        acc = await client.get(
            "https://api.cloudflare.com/client/v4/accounts",
            headers=headers
        )
        accounts = acc.json().get("result", [])
        if not accounts:
            raise HTTPException(status_code=400, detail="هیچ اکانتی پیدا نشد.")

        account_id = accounts[0]["id"]
        account_name = accounts[0]["name"]

        # 3. Build config
        config = build_config(req.worker_name, req.admin_path, req.uuid)

        # 4. Create D1 Database
        d1_resp = await client.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database",
            headers=headers,
            json={"name": config["d1_name"]}
        )
        d1_id = "auto"
        if d1_resp.status_code in (200, 201):
            d1_id = d1_resp.json().get("result", {}).get("uuid", "auto")

        # 5. Fetch Zeus source + obfuscate
        source = await fetch_zeus_source()
        obfuscated = obfuscate_worker_js(source, config)

        # 6. Upload Worker
        metadata = {
            "main_module": "_worker.js",
            "bindings": [
                {"type": "d1", "name": "DB", "id": d1_id},
                {"type": "plain_text", "name": "ADMIN_PATH", "text": config["admin_path"]},
                {"type": "plain_text", "name": "UUID", "text": config["uuid"]},
            ],
            "compatibility_date": "2026-01-20",
            "compatibility_flags": ["nodejs_compat"]
        }

        upload = await client.put(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/scripts/{config['worker_name']}",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "_worker.js": ("_worker.js", obfuscated, "application/javascript+module"),
            }
        )
        if upload.status_code not in (200, 201):
            raise HTTPException(
                status_code=upload.status_code,
                detail=f"خطا در استقرار Worker: {upload.text}"
            )

        # 7. Enable workers.dev subdomain
        await client.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/scripts/{config['worker_name']}/subdomain",
            headers=headers,
            json={"enabled": True}
        )

        # 8. Get subdomain
        sub = await client.get(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/subdomain",
            headers=headers
        )
        subdomain = "workers.dev"
        if sub.status_code == 200:
            subdomain = sub.json().get("result", {}).get("subdomain", "workers.dev")

        live = f"{config['worker_name']}.{subdomain}.workers.dev"

        return {
            "status": "success",
            "account_name": account_name,
            "worker_name": config["worker_name"],
            "d1_name": config["d1_name"],
            "admin_path": config["admin_path"],
            "uuid": config["uuid"],
            "panel_url": f"https://{live}{config['admin_path']}",
            "sub_url": f"https://{live}/sub/{config['uuid']}",
            "live_url": f"https://{live}",
        }


@app.post("/api/download")
async def download_zip(req: DownloadRequest):
    config = build_config(req.worker_name, req.admin_path, req.uuid)
    source = await fetch_zeus_source()
    obfuscated = obfuscate_worker_js(source, config)
    wrangler = generate_wrangler_toml(
        config["worker_name"], config["d1_name"],
        config["admin_path"], config["uuid"]
    )
    readme = generate_readme(config["worker_name"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("_worker.js", obfuscated)
        z.writestr("wrangler.toml", wrangler)
        z.writestr("README.md", readme)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={config['worker_name']}.zip"}
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
