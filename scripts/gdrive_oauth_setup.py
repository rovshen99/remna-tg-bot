import os
import pathlib

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
ENV_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET_FILE"
ENV_TOKEN_OUTPUT = "GOOGLE_OAUTH_TOKEN_FILE"


def main():
    load_dotenv()

    client_secret_path = pathlib.Path(os.getenv(ENV_CLIENT_SECRET, "credentials/desktop-client.json"))
    token_output_path = pathlib.Path(os.getenv(ENV_TOKEN_OUTPUT, "credentials/google-oauth-token.json"))

    if not client_secret_path.exists():
        raise FileNotFoundError(
            f"Client secret file not found: {client_secret_path}. "
            f"Set {ENV_CLIENT_SECRET} or place the JSON in credentials/."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        client_secret_path,
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    token_output_path.parent.mkdir(parents=True, exist_ok=True)
    token_output_path.write_text(creds.to_json())
    print(f"Token saved to {token_output_path}")


if __name__ == "__main__":
    main()
