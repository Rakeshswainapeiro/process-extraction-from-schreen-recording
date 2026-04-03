"""Settings routes for managing AI model configurations."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AIModelConfig, get_db
from app.routes.auth_routes import require_user
from app.services.encryption_service import get_encryption_service

router = APIRouter(prefix="/api/settings")


def _safe_key_preview(api_key: str) -> str:
    """Return a safe preview of an API key — never the real value."""
    if len(api_key) > 12:
        return api_key[:6] + "..." + api_key[-4:]
    return "***"


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    """List all AI model configs for the current user. Never returns plaintext API keys."""
    result = await db.execute(
        select(AIModelConfig)
        .where(AIModelConfig.user_id == user.id)
        .order_by(AIModelConfig.created_at.desc())
    )
    configs = result.scalars().all()
    return JSONResponse([{
        "id": c.id,
        "provider": c.provider,
        "name": c.name,
        "api_key_preview": _safe_key_preview(c.api_key),
        "base_url": c.base_url,
        "model_id": c.model_id,
        "max_tokens": c.max_tokens,
        "is_active": c.is_active,
        "is_default": c.is_default,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    } for c in configs])


@router.get("/models/active")
async def get_active_model(db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    """Return the currently active/default model for the UI switcher."""
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.user_id == user.id,
            AIModelConfig.is_active == True,
        ).order_by(
            AIModelConfig.is_default.desc(),
            AIModelConfig.created_at.desc(),
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        return JSONResponse({"id": None, "name": "Platform Default", "model_id": None, "provider": None, "is_default": False})
    return JSONResponse({
        "id": cfg.id,
        "name": cfg.name,
        "model_id": cfg.model_id,
        "provider": cfg.provider,
        "is_default": cfg.is_default,
        "max_tokens": cfg.max_tokens,
    })


@router.post("/models/active")
async def set_active_model(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Switch the active model for this session (sets it as default)."""
    data = await request.json()
    config_id = data.get("config_id")
    if not config_id:
        raise HTTPException(400, "config_id is required")

    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.id == config_id,
            AIModelConfig.user_id == user.id,
        )
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(404, "Model config not found")

    # Unset all defaults, then set selected
    await db.execute(
        update(AIModelConfig)
        .where(AIModelConfig.user_id == user.id)
        .values(is_default=False)
    )
    cfg.is_default = True
    await db.commit()
    return JSONResponse({"status": "switched", "id": cfg.id, "name": cfg.name})


@router.post("/models")
async def add_model(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Add a new AI model configuration. API key is encrypted before storage."""
    data = await request.json()
    enc = get_encryption_service()

    api_key = data.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required")

    model_id = data.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="Model ID is required")

    # Validate base_url for custom provider — block SSRF targets
    base_url = data.get("base_url", "").strip() or None
    if base_url:
        _validate_base_url(base_url)

    is_default = data.get("is_default", False)

    if is_default:
        await db.execute(
            update(AIModelConfig)
            .where(AIModelConfig.user_id == user.id, AIModelConfig.is_default == True)
            .values(is_default=False)
        )

    config = AIModelConfig(
        user_id=user.id,
        provider=data.get("provider", "anthropic"),
        name=data.get("name", "My Model"),
        api_key=enc.encrypt(api_key),
        is_encrypted=True,
        base_url=base_url,
        model_id=model_id,
        max_tokens=data.get("max_tokens", 8000),
        is_active=True,
        is_default=is_default,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    return JSONResponse({
        "id": config.id,
        "status": "created",
        "name": config.name,
        "model_id": config.model_id,
    })


@router.put("/models/{config_id}")
async def update_model(
    config_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Update an existing AI model configuration."""
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.id == config_id,
            AIModelConfig.user_id == user.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")

    data = await request.json()
    enc = get_encryption_service()

    if "name" in data:
        config.name = data["name"]
    if "provider" in data:
        config.provider = data["provider"]
    if "api_key" in data and data["api_key"].strip():
        config.api_key = enc.encrypt(data["api_key"].strip())
        config.is_encrypted = True
    if "base_url" in data:
        base_url = data["base_url"].strip() or None
        if base_url:
            _validate_base_url(base_url)
        config.base_url = base_url
    if "model_id" in data and data["model_id"].strip():
        config.model_id = data["model_id"].strip()
    if "max_tokens" in data:
        config.max_tokens = data["max_tokens"]
    if "is_active" in data:
        config.is_active = data["is_active"]

    if data.get("is_default"):
        await db.execute(
            update(AIModelConfig)
            .where(AIModelConfig.user_id == user.id, AIModelConfig.id != config_id)
            .values(is_default=False)
        )
        config.is_default = True

    await db.commit()
    return JSONResponse({"status": "updated", "id": config.id})


@router.delete("/models/{config_id}")
async def delete_model(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Delete an AI model configuration."""
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.id == config_id,
            AIModelConfig.user_id == user.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")

    await db.delete(config)
    await db.commit()
    return JSONResponse({"status": "deleted"})


@router.post("/models/{config_id}/set-default")
async def set_default_model(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Set a model configuration as the default."""
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.id == config_id,
            AIModelConfig.user_id == user.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")

    await db.execute(
        update(AIModelConfig)
        .where(AIModelConfig.user_id == user.id)
        .values(is_default=False)
    )
    config.is_default = True
    await db.commit()
    return JSONResponse({"status": "default_set", "id": config.id})


@router.post("/models/test")
async def test_model_connection(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Test connectivity to an AI model endpoint."""
    data = await request.json()
    provider = data.get("provider", "anthropic")
    api_key = data.get("api_key", "").strip()
    base_url = data.get("base_url", "").strip() or None
    model_id = data.get("model_id", "").strip()

    if not api_key:
        return JSONResponse({"status": "error", "message": "API key is required"}, status_code=400)

    if base_url:
        try:
            _validate_base_url(base_url)
        except HTTPException as e:
            return JSONResponse({"status": "error", "message": e.detail}, status_code=400)

    try:
        if provider == "anthropic":
            import anthropic as _anthropic
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = _anthropic.Anthropic(**kwargs)
            message = client.messages.create(
                model=model_id or "claude-sonnet-4-6",
                max_tokens=50,
                messages=[{"role": "user", "content": "Reply with exactly: CONNECTION_OK"}],
            )
            response_text = message.content[0].text
            return JSONResponse({
                "status": "success",
                "message": f"Connected successfully. Model responded: {response_text[:100]}",
                "model": message.model,
            })

        elif provider in ("openai", "custom"):
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            endpoint = (base_url or "https://api.openai.com/v1").rstrip("/")
            async with httpx.AsyncClient(timeout=30) as http_client:
                resp = await http_client.post(
                    f"{endpoint}/chat/completions",
                    headers=headers,
                    json={
                        "model": model_id or "gpt-4",
                        "messages": [{"role": "user", "content": "Reply with exactly: CONNECTION_OK"}],
                        "max_tokens": 50,
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result["choices"][0]["message"]["content"]
                    return JSONResponse({
                        "status": "success",
                        "message": f"Connected successfully. Model responded: {text[:100]}",
                        "model": result.get("model", model_id),
                    })
                else:
                    return JSONResponse({
                        "status": "error",
                        "message": f"API returned status {resp.status_code}: {resp.text[:200]}",
                    }, status_code=400)

        else:
            return JSONResponse({"status": "error", "message": f"Unknown provider: {provider}"}, status_code=400)

    except Exception as e:
        return JSONResponse({
            "status": "error",
            "message": f"Connection failed: {str(e)}",
        }, status_code=400)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_base_url(url: str) -> None:
    """Block SSRF-prone private/loopback addresses in custom base URLs."""
    import ipaddress, urllib.parse
    blocked_hosts = {"localhost", "metadata.google.internal"}
    blocked_prefixes = ("169.254.", "::1")
    blocked_networks = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
    ]
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if host in blocked_hosts:
            raise HTTPException(400, f"Base URL host '{host}' is not allowed")
        for prefix in blocked_prefixes:
            if host.startswith(prefix):
                raise HTTPException(400, f"Base URL host '{host}' is not allowed")
        try:
            addr = ipaddress.ip_address(host)
            for net in blocked_networks:
                if addr in net:
                    raise HTTPException(400, f"Base URL host '{host}' is a private address and not allowed")
        except ValueError:
            pass  # hostname, not an IP — allow
    except HTTPException:
        raise
    except Exception:
        pass  # malformed URL — let the actual HTTP request fail naturally


async def get_active_model_config(db: AsyncSession, user_id: int):
    """Get the user's default/active model configuration (used by other services)."""
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.user_id == user_id,
            AIModelConfig.is_default == True,
            AIModelConfig.is_active == True,
        )
    )
    config = result.scalar_one_or_none()
    if config:
        return config
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.user_id == user_id,
            AIModelConfig.is_active == True,
        ).order_by(AIModelConfig.created_at.desc())
    )
    return result.scalar_one_or_none()
