import requests

url = "https://www.stw.berlin/xhr/speiseplan-wochentag.html"

data = {
    "resources_id": "191",
    "date": "2025-11-14",   # date to get the menu
    "week": "46"            # seems to not do much, can be empty
}

headers = {
    "User-Agent": "Mozilla/5.0"
}

resp = requests.post(url, data=data, headers=headers)
print(resp.status_code)
