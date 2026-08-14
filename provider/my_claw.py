from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError


class MyClawProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict) -> None:
        # No credentials required; presence check only.
        return
