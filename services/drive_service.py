"""
Google Drive is the system of record for every generated variant (doc §7).
This service creates the standard numbered folder tree per new account and
handles numbered uploads (logo_1.png, reels_script_5.md, ...).
"""
from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import settings, DRIVE_FOLDER_TEMPLATE

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_client():
    creds = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def create_account_folder_tree(account_display_name: str) -> str:
    """
    Creates /<AccountName>/ under the farm root, then every subfolder from
    DRIVE_FOLDER_TEMPLATE (handles nested paths like '04_карусели/шаблон').
    Returns the root folder id for this account — store it on Account.drive_folder_id.
    """
    drive = _get_drive_client()

    root = drive.files().create(
        body={
            "name": account_display_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [settings.GOOGLE_DRIVE_ROOT_FOLDER_ID],
        },
        fields="id",
    ).execute()
    root_id = root["id"]

    created: dict[str, str] = {"": root_id}
    for path in DRIVE_FOLDER_TEMPLATE:
        parts = path.split("/")
        parent_key = ""
        for part in parts:
            key = f"{parent_key}/{part}" if parent_key else part
            if key not in created:
                folder = drive.files().create(
                    body={
                        "name": part,
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [created[parent_key]],
                    },
                    fields="id",
                ).execute()
                created[key] = folder["id"]
            parent_key = key

    return root_id


def upload_numbered_file(parent_folder_id: str, filename: str, local_path: str, mime_type: str) -> str:
    """Uploads e.g. logo_3.png into a given subfolder and returns the Drive file id."""
    from googleapiclient.http import MediaFileUpload

    drive = _get_drive_client()
    media = MediaFileUpload(local_path, mimetype=mime_type)
    file = drive.files().create(
        body={"name": filename, "parents": [parent_folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return file["id"]


def list_numbered_files(folder_id: str) -> list[dict]:
    """Returns [{id, name}] so the bot can map 'number -> file' for approvals."""
    drive = _get_drive_client()
    results = drive.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="files(id, name)",
        orderBy="name",
    ).execute()
    return results.get("files", [])
