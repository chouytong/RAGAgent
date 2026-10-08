from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ragagent.api.schemas import HealthResult, ProviderTest
from ragagent.errors import ApplicationError, error_payload
from ragagent.providers.chat import LiteLLMProvider, usage_record
from ragagent.providers.config import ProviderConfig, load_config
from ragagent.providers.environment import runtime_value
from ragagent.settings import get_settings

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
def providers() -> dict[str, object]:
    config = load_config(get_settings().agent_config)
    agents = {}
    for name in ["supervisor", "retriever", "analyst", "reviewer"]:
        model = getattr(config.agents, name)
        agents[name] = {
            **model.model_dump(),
            "key_configured": bool(runtime_value(model.key_environment))
            if model.key_environment
            else True,
        }
    return {"agents": agents}


@router.put("")
def update_config(config: ProviderConfig) -> dict[str, str]:
    path = get_settings().agent_config
    temporary = path.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2))  # JSON is also valid YAML.
    temporary.replace(path)
    return {"status": "saved"}


@router.post("/test")
async def test(request: ProviderTest) -> JSONResponse:
    model = getattr(load_config(get_settings().agent_config).agents, request.agent)
    provider = LiteLLMProvider(model, timeout=15)
    try:
        result = await provider.complete(
            'Connectivity test. Return {"ok": true}.', {}, HealthResult
        )
        return JSONResponse(
            content={
                "ok": result.ok,
                "agent": request.agent,
                "model": model.model,
                "usage": {"chat": usage_record(provider.usage)},
                "usage_scope": "current_request",
            }
        )
    except ApplicationError as exc:
        return JSONResponse(
            status_code=503,
            content={
                **error_payload(exc.code),
                "usage": {"chat": usage_record(provider.usage)},
                "usage_scope": "current_request",
            },
        )
