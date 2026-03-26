"""Settings routes for managing AI model configurations."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AIModelConfig, get_db
from app.routes.auth_routes import require_user

router = APIRouter(prefix="/api/settings")


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    """List all AI model configs for the current user."""
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
        "api_key_preview": c.api_key[:8] + "..." + c.api_key[-4:] if len(c.api_key) > 12 else "***",
        "base_url": c.base_url,
        "model_id": c.model_id,
        "max_tokens": c.max_tokens,
        "is_active": c.is_active,
        "is_default": c.is_default,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    } for c in configs])


@router.post("/models")
async def add_model(request: Request, db: AsyncSession = Depends(get_db),
                    user=Depends(require_user)):
    """Add a new AI model configuration."""
    data = await request.json()

    api_key = data.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required")

    model_id = data.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="Model ID is required")

    is_default = data.get("is_default", False)

    # If this is set as default, unset any existing defaults for this user
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
        api_key=api_key,
        base_url=data.get("base_url", "").strip() or None,
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
async def update_model(config_id: int, request: Request,
                       db: AsyncSession = Depends(get_db), user=Depends(require_user)):
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

    if "name" in data:
        config.name = data["name"]
    if "provider" in data:
        config.provider = data["provider"]
    if "api_key" in data and data["api_key"].strip():
        config.api_key = data["api_key"].strip()
    if "base_url" in data:
        config.base_url = data["base_url"].strip() or None
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
async def delete_model(config_id: int, db: AsyncSession = Depends(get_db),
                       user=Depends(require_user)):
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
async def set_default_model(config_id: int, db: AsyncSession = Depends(get_db),
                            user=Depends(require_user)):
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

    # Unset all defaults, then set this one
    await db.execute(
        update(AIModelConfig)
        .where(AIModelConfig.user_id == user.id)
        .values(is_default=False)
    )
    config.is_default = True
    await db.commit()
    return JSONResponse({"status": "default_set", "id": config.id})


@router.post("/models/test")
async def test_model_connection(request: Request, db: AsyncSession = Depends(get_db),
                                user=Depends(require_user)):
    """Test connectivity to an AI model endpoint."""
    data = await request.json()
    provider = data.get("provider", "anthropic")
    api_key = data.get("api_key", "").strip()
    base_url = data.get("base_url", "").strip() or None
    model_id = data.get("model_id", "").strip()

    if not api_key:
        return JSONResponse({"status": "error", "message": "API key is required"}, status_code=400)

    try:
        if provider == "anthropic":
            import anthropic
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = anthropic.Anthropic(**kwargs)
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

        elif provider == "openai":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            endpoint = base_url or "https://api.openai.com/v1"
            endpoint = endpoint.rstrip("/")
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

        elif provider == "custom":
            # Generic OpenAI-compatible endpoint
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            if not base_url:
                return JSONResponse({"status": "error", "message": "Base URL is required for custom endpoints"}, status_code=400)

            endpoint = base_url.rstrip("/")
            async with httpx.AsyncClient(timeout=30) as http_client:
                resp = await http_client.post(
                    f"{endpoint}/chat/completions",
                    headers=headers,
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": "Reply with exactly: CONNECTION_OK"}],
                        "max_tokens": 50,
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("choices", [{}])[0].get("message", {}).get("content", "OK")
                    return JSONResponse({
                        "status": "success",
                        "message": f"Connected successfully. Response: {text[:100]}",
                        "model": model_id,
                    })
                else:
                    return JSONResponse({
                        "status": "error",
                        "message": f"Endpoint returned status {resp.status_code}: {resp.text[:200]}",
                    }, status_code=400)

        else:
            return JSONResponse({"status": "error", "message": f"Unknown provider: {provider}"}, status_code=400)

    except Exception as e:
        return JSONResponse({
            "status": "error",
            "message": f"Connection failed: {str(e)}",
        }, status_code=400)


async def get_active_model_config(db: AsyncSession, user_id: int):
    """Get the user's default/active model configuration."""
    # First try default
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

    # Fall back to any active config
    result = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.user_id == user_id,
            AIModelConfig.is_active == True,
        ).order_by(AIModelConfig.created_at.desc())
    )
    return result.scalar_one_or_none()
