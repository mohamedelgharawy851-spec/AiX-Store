from __future__ import annotations

from dotenv import load_dotenv
load_dotenv(dotenv_path=__file__ and __import__('pathlib').Path(__file__).parents[3] / '.env')

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse

from .ai.config import AI_ENABLED, AI_MODE, AI_MODEL_ID, ai_pipeline_is_enabled
from .ai.model_manager import model_manager
from .ai.rewrite_service import generate_rewrite_plan
from .ai.category_judge import judge_ambiguous_category
from .config import DEFAULT_BOOTSTRAP_COUNT, DEFAULT_PAGE_SIZE, IMAGE_CACHE_DIR, SERVICE_NAME
from .discovery import apify_client
from .jobs import job_runner
from .storage.db import (
    add_user_favorite,
    authenticate_user,
    create_user,
    get_auth_context_by_token,
    list_discovery_hits,
    list_user_favorites,
    get_source_image_url,
    get_user_by_token,
    get_user_id_by_token,
    initialize_database,
    list_products,
    list_user_history,
    list_user_recommendations,
    logout_user,
    record_user_event,
    remove_user_favorite,
)
from .storage.images import cache_image, prepare_image_cache_dir, resolve_image_path


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    prepare_image_cache_dir()
    yield


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)


def _extract_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _require_user_id(authorization: str | None) -> str:
    token = _extract_token(authorization)
    user_id = get_user_id_by_token(token or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user_id


def _require_auth_context(authorization: str | None) -> dict[str, str]:
    auth_context = get_auth_context_by_token(_extract_token(authorization) or "")
    if not auth_context:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return auth_context


def _extract_client_session_id(value: str | None) -> str | None:
    session_id = (value or "").strip()
    return session_id or None


def _effective_session_id(raw_value: str | None, auth_context: dict[str, str] | None = None) -> str | None:
    return _extract_client_session_id(raw_value) or (auth_context or {}).get("session_id")


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/ai/health")
async def ai_health():
    return {
        "enabled": AI_ENABLED,
        "mode": AI_MODE,
        "modelId": AI_MODEL_ID if AI_ENABLED else None,
        "pipelineEnabled": ai_pipeline_is_enabled(),
        **model_manager.status(),
    }


@app.get("/discovery/health")
async def discovery_health():
    return await apify_client.health()


@app.get("/catalog/bootstrap")
async def bootstrap_catalog(
    count: int = Query(DEFAULT_BOOTSTRAP_COUNT, ge=1, le=200),
    authorization: str | None = Header(default=None),
):
    user_id = get_user_id_by_token(_extract_token(authorization) or "")
    return await job_runner.ensure_bootstrap(count, user_id=user_id)


@app.get("/catalog/products")
async def catalog_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, alias="pageSize", ge=1, le=100),
    category_id: str | None = Query(None, alias="category"),
    authorization: str | None = Header(default=None),
):
    user_id = get_user_id_by_token(_extract_token(authorization) or "")
    if category_id:
        return await job_runner.list_category(category_id=category_id, page=page, page_size=page_size, user_id=user_id)
    return list_products(page=page, page_size=page_size, category_id=None, user_id=user_id)


@app.get("/catalog/search")
async def catalog_search(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, alias="pageSize", ge=1, le=100),
    category_id: str | None = Query(None, alias="category"),
    authorization: str | None = Header(default=None),
):
    user_id = get_user_id_by_token(_extract_token(authorization) or "")
    return await job_runner.search(query=q, page=page, page_size=page_size, category_id=category_id, user_id=user_id)


@app.post("/discovery/query")
async def discovery_query(payload: dict):
    raw_queries = payload.get("queries")
    queries: list[str] = []
    if isinstance(raw_queries, list):
        queries = [str(item).strip() for item in raw_queries if str(item).strip()]
    if not queries:
        query = str(payload.get("query", "")).strip()
        if query:
            queries = [query]
    if not queries:
        raise HTTPException(status_code=400, detail="Query is required")
    category_id = str(payload.get("categoryId", "")).strip() or None
    result = await apify_client.search(query_variants=queries, category_id=category_id)
    return result.to_json()


@app.get("/discovery/cache")
async def discovery_cache(context_key: str = Query(..., alias="contextKey")):
    return {"items": list_discovery_hits(context_key)}


@app.post("/ai/rewrite")
async def ai_rewrite(payload: dict):
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")
    category_id = str(payload.get("categoryId", "")).strip() or None
    trigger_reason = str(payload.get("triggerReason", "")).strip() or "manual"
    deterministic_variants = payload.get("deterministicVariants")
    if not isinstance(deterministic_variants, list):
        deterministic_variants = [query]
    return await generate_rewrite_plan(
        query=query,
        category_id=category_id,
        trigger_reason=trigger_reason,
        deterministic_variants=[str(item) for item in deterministic_variants if str(item).strip()],
    )


@app.post("/ai/judge-category")
async def ai_judge_category(payload: dict):
    title = str(payload.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    description = str(payload.get("description", "")).strip()
    brand = str(payload.get("brand", "")).strip() or None
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    provider_name = str(payload.get("providerName", "")).strip() or "unknown"
    source_category_id = str(payload.get("sourceCategoryId", "")).strip() or None
    rule_classification = payload.get("ruleClassification")
    if not isinstance(rule_classification, dict):
        raise HTTPException(status_code=400, detail="ruleClassification is required")
    return judge_ambiguous_category(
        title=title,
        description=description,
        brand=brand,
        tags=[str(item) for item in tags if str(item).strip()],
        provider_name=provider_name,
        source_category_id=source_category_id,
        rule_classification=rule_classification,
    )


@app.get("/catalog/products/{product_id}")
async def catalog_product_detail(
    product_id: str,
    authorization: str | None = Header(default=None),
    x_aixstore_session: str | None = Header(default=None, alias="X-AIXStore-Session"),
):
    auth_context = get_auth_context_by_token(_extract_token(authorization) or "", touch=False)
    user_id = str(auth_context["user_id"]) if auth_context else None
    payload = await job_runner.get_detail(
        product_id,
        user_id=user_id,
        session_id=_effective_session_id(x_aixstore_session, auth_context),
    )
    if not payload:
        raise HTTPException(status_code=404, detail="Product not found")
    return payload


@app.get("/catalog/products/{product_id}/related")
async def catalog_product_related(
    product_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, alias="pageSize", ge=1, le=100),
    authorization: str | None = Header(default=None),
    x_aixstore_session: str | None = Header(default=None, alias="X-AIXStore-Session"),
):
    auth_context = get_auth_context_by_token(_extract_token(authorization) or "", touch=False)
    user_id = str(auth_context["user_id"]) if auth_context else None
    payload = await job_runner.get_related(
        product_id,
        page=page,
        page_size=page_size,
        user_id=user_id,
        session_id=_effective_session_id(x_aixstore_session, auth_context),
    )
    if not payload:
        raise HTTPException(status_code=404, detail="Product not found")
    return payload


@app.post("/auth/signup")
async def auth_signup(payload: dict):
    try:
        return create_user(str(payload.get("email", "")), str(payload.get("password", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/auth/login")
async def auth_login(payload: dict):
    try:
        result = authenticate_user(str(payload.get("email", "")), str(payload.get("password", "")))
        user_id = get_user_id_by_token(result["token"])
        if user_id:
            record_user_event(user_id, "login")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/auth/logout")
async def auth_logout(authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    logout_user(token)
    return {"ok": True}


@app.get("/me")
async def me(authorization: str | None = Header(default=None)):
    token = _extract_token(authorization)
    user = get_user_by_token(token or "", touch=True)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


@app.get("/me/history")
async def me_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, alias="pageSize", ge=1, le=100),
    authorization: str | None = Header(default=None),
    x_aixstore_session: str | None = Header(default=None, alias="X-AIXStore-Session"),
):
    auth_context = _require_auth_context(authorization)
    return list_user_history(
        auth_context["user_id"],
        page=page,
        page_size=page_size,
        session_id=_effective_session_id(x_aixstore_session, auth_context),
    )


@app.get("/me/favorites")
async def me_favorites(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, alias="pageSize", ge=1, le=100),
    authorization: str | None = Header(default=None),
):
    user_id = _require_user_id(authorization)
    return list_user_favorites(user_id, page=page, page_size=page_size)


@app.put("/me/favorites/{product_id}")
async def me_favorite_put(product_id: str, authorization: str | None = Header(default=None)):
    user_id = _require_user_id(authorization)
    try:
        return add_user_favorite(user_id, product_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/me/favorites/{product_id}")
async def me_favorite_delete(product_id: str, authorization: str | None = Header(default=None)):
    user_id = _require_user_id(authorization)
    return {"ok": remove_user_favorite(user_id, product_id)}


@app.get("/me/recommendations")
async def me_recommendations(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, alias="pageSize", ge=1, le=100),
    authorization: str | None = Header(default=None),
    x_aixstore_session: str | None = Header(default=None, alias="X-AIXStore-Session"),
):
    auth_context = _require_auth_context(authorization)
    return list_user_recommendations(
        auth_context["user_id"],
        page=page,
        page_size=page_size,
        session_id=_effective_session_id(x_aixstore_session, auth_context),
    )


@app.post("/me/events")
async def me_events(
    payload: dict,
    authorization: str | None = Header(default=None),
    x_aixstore_session: str | None = Header(default=None, alias="X-AIXStore-Session"),
):
    auth_context = _require_auth_context(authorization)
    event_type = str(payload.get("type", "")).strip()
    if not event_type:
        raise HTTPException(status_code=400, detail="Event type is required")
    record_user_event(
        user_id=auth_context["user_id"],
        event_type=event_type,
        product_id=payload.get("productId"),
        category_id=payload.get("categoryId"),
        query_text=payload.get("queryText"),
        source_url=payload.get("sourceUrl"),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        session_id=_effective_session_id(x_aixstore_session, auth_context),
    )
    return {"ok": True}


@app.get("/images/{local_image_key}")
async def cached_image(local_image_key: str):
    file_path = resolve_image_path(local_image_key)
    if not file_path:
        source_image_url = get_source_image_url(local_image_key)
        if source_image_url:
            await cache_image(source_image_url)
            file_path = resolve_image_path(local_image_key)
    if not file_path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    return JSONResponse(status_code=500, content={"error": "python-service-failed", "detail": str(exc)})
