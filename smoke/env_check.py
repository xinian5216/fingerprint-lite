"""Print the exact verification environment (Python + key package versions)."""

import platform
import sys
from importlib.metadata import version


def main() -> None:
    print(f"python={sys.version.split()[0]} ({platform.machine()})")
    print(f"os={platform.system()} {platform.release()} build={platform.version()}")
    for pkg in (
        "camoufox",
        "playwright",
        "camoufox-profile-manager",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "cryptography",
        "httpx",
        "argon2-cffi",
    ):
        try:
            print(f"{pkg}={version(pkg)}")
        except Exception as exc:  # noqa: BLE001
            print(f"{pkg}=ERROR {exc}")


if __name__ == "__main__":
    main()
