import json
import os
import subprocess

import numpy as np
import pandas as pd

from apl_connection import get_apl_credentials
from models.flat_file_hana_apl import train_and_forecast_hana_apl


def cf_output(*args):
    return subprocess.check_output(
        ["cf", *args],
        text=True,
        env={**os.environ, "CF_TRACE": "false"},
    ).strip()


# Read the application's service bindings without printing credentials.
app_guid = cf_output("app", "TIME_SERIES_FORECAST-python", "--guid")
environment = json.loads(cf_output("curl", f"/v3/apps/{app_guid}/env"))

services = environment.get("system_env_json", {}).get("VCAP_SERVICES")
if not services:
    raise RuntimeError("Could not retrieve the application's service bindings.")

os.environ["VCAP_SERVICES"] = json.dumps(services)
credentials = get_apl_credentials()

print(
    f"Testing APL binding: host={credentials['host']}, "
    f"port={credentials['port']}, user={credentials['user']}, "
    f"schema={credentials['schema']}"
)

days = np.arange(365)
history = pd.DataFrame({
    "DCSID": "APL_TEST",
    "MIC": "TEST",
    "PRICEDATE": pd.date_range("2024-01-01", periods=365, freq="D"),
    "PRICE": 100 + 0.04 * days + 5 * np.sin(2 * np.pi * days / 7),
})

forecasts = train_and_forecast_hana_apl(
    df=history,
    dcsid="APL_TEST",
    mic="TEST",
    credentials=credentials,
    horizon=7,
)

print(pd.DataFrame(forecasts).to_string(index=False))
print("SUCCESS: saved service credentials and updated APL model work.")