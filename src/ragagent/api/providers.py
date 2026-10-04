from fastapi import APIRouter, HTTPException

from ragagent.api.schemas import HealthResult, ProviderTest
from ragagent.errors import ApplicationError
from ragagent.providers.chat import LiteLLMProvider
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
async def test(request: ProviderTest) -> dict[str, object]:
    model = getattr(load_config(get_settings().agent_config).agents, request.agent)
    provider = LiteLLMProvider(model, timeout=15)
    try:
        result = await provider.complete(
            'Connectivity test. Return {"ok": true}.', {}, HealthResult
        )
        return {"ok": result.ok, "agent": request.agent, "model": model.model}
    except ApplicationError as exc:
        raise HTTPException(503, exc.code) from None
