"""Model-provider interface and adapters."""

from quant_agent_os.providers.base import ModelProvider, ProviderError
from quant_agent_os.providers.fake import FakeModelProvider

__all__ = ["FakeModelProvider", "ModelProvider", "ProviderError"]
