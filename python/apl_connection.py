import json
import os


APL_SERVICE_NAME = "TIME_SERIES_FORECAST-apl"


def get_apl_credentials():
    services = json.loads(os.getenv("VCAP_SERVICES", "{}"))

    for service_list in services.values():
        for service in service_list:
            if service.get("name") == APL_SERVICE_NAME:
                credentials = service.get("credentials") or {}

                required = ("host", "port", "user", "password", "schema")
                missing = [
                    key for key in required
                    if not credentials.get(key)
                ]

                if missing:
                    raise RuntimeError(
                        "Missing APL credentials: " + ", ".join(missing)
                    )

                return credentials

    raise RuntimeError(
        f"Required APL service binding '{APL_SERVICE_NAME}' was not found."
    )