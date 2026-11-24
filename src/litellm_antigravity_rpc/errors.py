"""Public errors contain no upstream messages, prompts, or local paths."""


class GatewayError(Exception):
    def __init__(self, code="protocol_error", status=502):
        self.code = code
        self.status = status
        super().__init__(code)

    def payload(self):
        return {
            "error": {
                "message": self.code.replace("_", " "),
                "type": "invalid_request_error"
                if self.status == 400
                else "gateway_error",
                "code": self.code,
                "param": None,
            }
        }


def remote_error(error):
    if not isinstance(error, dict) or type(error.get("code")) is not int:
        return GatewayError()
    code = error["code"]
    if code == -32000:
        return GatewayError("upstream_authentication_required", 502)
    if code == -32602:
        return GatewayError("upstream_invalid_parameters", 400)
    if code == -32800:
        return GatewayError("upstream_cancelled", 502)
    # Only structured status, never guess quota from arbitrary error prose.
    data = error.get("data")
    if isinstance(data, dict) and data.get("status") == "RESOURCE_EXHAUSTED":
        return GatewayError("upstream_quota_exceeded", 429)
    return GatewayError("upstream_error", 502)
