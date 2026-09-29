"""Diagnose how Camoufox 0.5.6 hands the generated config to the launcher.

Windows limits environment variables to 32767 characters. If Camoufox splits
CAMOU_CONFIG into numbered parts, a client that reads only part 1 (as cp-m's
fingerprint_store.resolve does) cannot parse the JSON.
"""

import json
import re

from camoufox.utils import launch_options

opts = launch_options(
    os="windows",
    locale="en-US,en",
    config={},
    geoip=False,
    headless=True,
    humanize=True,
    i_know_what_im_doing=True,
)

env = opts.get("env", {})
keys = sorted([k for k in env if k.startswith("CAMOU")], key=lambda k: (len(k), k))
print("env keys:", keys)
for key in keys:
    print(f"{key}: {len(env[key])} chars")

indexed = {}
for key in keys:
    match = re.fullmatch(r"CAMOU_CONFIG_(\d+)", key)
    if match:
        indexed[int(match.group(1))] = env[key]
if indexed:
    parts = [indexed[i] for i in sorted(indexed)]
    joined = "".join(parts)
    print(f"parts={len(parts)} total_joined={len(joined)} chars")
    for label, text in (("CAMOU_CONFIG_1 alone", parts[0]), ("joined parts", joined)):
        try:
            parsed = json.loads(text)
            print(f"{label}: JSON OK, {len(parsed)} keys")
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: JSON FAILED -> {exc}")
else:
    single = env.get("CAMOU_CONFIG", "")
    print(f"single CAMOU_CONFIG: {len(single)} chars")
    try:
        parsed = json.loads(single)
        print(f"single CAMOU_CONFIG: JSON OK, {len(parsed)} keys")
    except Exception as exc:  # noqa: BLE001
        print(f"single CAMOU_CONFIG: JSON FAILED -> {exc}")
