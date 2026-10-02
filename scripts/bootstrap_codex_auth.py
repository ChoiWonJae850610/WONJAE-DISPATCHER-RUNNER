from __future__ import annotations

import os
from pathlib import Path

from openai_codex import Codex, CodexConfig

from wonjae_dispatcher_runner.auth_store import load_auth_json_for_rotation, prepare_codex_home


def main() -> int:
    codex_home = Path(os.environ["CODEX_HOME"])
    prepare_codex_home(codex_home)

    config = CodexConfig(
        env={
            "CODEX_HOME": str(codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
        }
    )
    with Codex(config=config) as codex:
        login = codex.login_chatgpt_device_code()
        print(f"OPENAI_VERIFICATION_URL={login.verification_url}", flush=True)
        print(f"OPENAI_USER_CODE={login.user_code}", flush=True)
        print("Authorize this one-time device code in your browser.", flush=True)
        completed = login.wait()
        if not completed.success:
            raise RuntimeError("ChatGPT device authorization did not complete successfully")
        account = codex.account(refresh_token=False)
        if account.account is None:
            raise RuntimeError("Codex account is missing after device authorization")

    # Validate presence and structure, but never print the credential payload.
    load_auth_json_for_rotation(codex_home)
    print("CODEX_AUTH_READY=true", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
