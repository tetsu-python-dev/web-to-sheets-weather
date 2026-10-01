import requests
import os

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

url = "https://musicbrainz.org/ws/2/release-group"

params = {
    "artist": "01830cc1-8a04-4dfb-9e4a-d557dfda6a93",
    "fmt": "json",
    "limit": 10
}

headers = {
    "User-Agent": "WebToSheetsPractice/1.0 (learning project)"
}

response = requests.get(url, params=params, headers=headers)

print("ステータスコード:", response.status_code)

data = response.json()

rows = [
    ["タイトル", "種類", "発売日"]
]

for item in data["release-groups"]:
    rows.append([
        item["title"],
        item.get("primary-type", ""),
        item.get("first-release-date", "")
    ])

print(rows)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = None

if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)

if not creds:
    flow = InstalledAppFlow.from_client_secrets_file(
        "credentials.json", SCOPES
    )
    creds = flow.run_local_server(port=0)

    with open("token.json", "w") as token:
        token.write(creds.to_json())

service = build("sheets", "v4", credentials=creds)

SPREADSHEET_ID = "YOUR_MUSIC_SPREADSHEET_ID"


result = service.spreadsheets().values().get(
    spreadsheetId=SPREADSHEET_ID,
    range="A2:A"
).execute()

existing_rows = result.get("values", [])

existing_titles = [row[0] for row in existing_rows if row]

print("登録済みタイトル数:", len(existing_titles))

new_rows = []

for item in data["release-groups"]:
    title = item["title"]

    if title in existing_titles:
        print("登録済み:", title)
    else:
        print("新規:", title)

        new_rows.append([
            title,
            item.get("primary-type", ""),
            item.get("first-release-date", "")
        ])

print("新規作品数:", len(new_rows))

if new_rows:
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="A:C",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": new_rows}
    ).execute()

    print("新規作品を追加しました！")
else:
    print("追加する新規作品はありません")

print("現在スプレッドシートにあるタイトル:")
print(existing_rows)