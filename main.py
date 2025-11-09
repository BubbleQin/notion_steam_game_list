import argparse
import requests
import time
import os
import logging
from features.review import get_steam_review_info
from features.steamstore import get_steam_store_info

# CONFIG
STEAM_API_KEY = os.environ.get("STEAM_API_KEY")
STEAM_USER_ID = os.environ.get("STEAM_USER_ID")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

include_played_free_games = os.environ.get("include_played_free_games") or "true"

# MISC
MAX_RETRIES = 20
RETRY_DELAY = 2


def send_request_with_retry(url, headers=None, json_data=None, retries=MAX_RETRIES, method="patch"):
    response = None
    while retries > 0:
        try:
            if method == "patch":
                response = requests.patch(url, headers=headers, json=json_data)
            elif method == "post":
                response = requests.post(url, headers=headers, json=json_data)
            elif method == "get":
                response = requests.get(url)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            text = getattr(response, "text", "")
            logger.error(f"HTTP error: {e} {text}")
            retries -= 1
            if retries > 0:
                time.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries exceeded.")
                return None


def get_owned_game_data_from_steam():
    url = (
        "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
        f"?key={STEAM_API_KEY}&steamid={STEAM_USER_ID}&include_appinfo=True"
    )
    if include_played_free_games == "true":
        url += "&include_played_free_games=True"

    response = send_request_with_retry(url, method="get")
    if not response:
        return None
    return response.json()


def query_achievements_info_from_steam(game):
    url = (
        "http://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v0001/"
        f"?key={STEAM_API_KEY}&steamid={STEAM_USER_ID}&appid={game['appid']}"
    )
    try:
        r = requests.get(url)
        r.raise_for_status()
        return r.json()
    except:
        return None


def get_achievements_count(game):
    info = {"total": 0, "achieved": 0}
    data = query_achievements_info_from_steam(game)
    if not data or not data.get("playerstats", {}).get("success", True):
        info["total"] = -1
        info["achieved"] = -1
        return info
    achs = data["playerstats"].get("achievements", [])
    for a in achs:
        info["total"] += 1
        if a.get("achieved"):
            info["achieved"] += 1
    return info


def query_item_from_notion_database(game):
    """按 name 或 store url 查询，找到现有页面"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    store_url = f"https://store.steampowered.com/app/{game['appid']}"
    data = {
        "filter": {
            "or": [
                {"property": "name", "title": {"equals": game["name"]}},
                {"property": "store url", "url": {"equals": store_url}},
            ]
        }
    }
    resp = send_request_with_retry(url, headers=headers, json_data=data, method="post")
    return resp.json() if resp else {"results": []}


def update_item_to_notion_database(page_id, game, achievements_info, review_text, steam_store_data):
    """更新 Notion 页面内容"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    playtime = round(float(game["playtime_forever"]) / 60, 1)
    last_played_time = None
    if game.get("rtime_last_played", 0) > 0:
        last_played_time = time.strftime("%Y-%m-%d", time.localtime(game["rtime_last_played"]))

    store_url = f"https://store.steampowered.com/app/{game['appid']}"
    icon_url = f"https://media.steampowered.com/steamcommunity/public/images/apps/{game['appid']}/{game['img_icon_url']}.jpg"
    cover_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{game['appid']}/header.jpg"

    total = achievements_info["total"]
    achieved = achievements_info["achieved"]
    completion = round((achieved / total) * 100, 1) if total > 0 else -1

    data = {
        "properties": {
            "name": {"title": [{"type": "text", "text": {"content": game["name"]}}]},
            "playtime": {"number": playtime},
            "last play": {"date": {"start": last_played_time} if last_played_time else None},
            "store url": {"url": store_url},
            "completion": {"number": completion},
            "total achievements": {"number": total},
            "achieved achievements": {"number": achieved},
            "review": {"rich_text": [{"type": "text", "text": {"content": review_text}}]},
            "info": {"rich_text": [{"type": "text", "text": {"content": steam_store_data["info"]}}]},
            "tags": {"multi_select": steam_store_data["tag"]},
        },
        "cover": {"type": "external", "external": {"url": cover_url}},
        "icon": {"type": "external", "external": {"url": icon_url}},
    }
    send_request_with_retry(url, headers=headers, json_data=data, method="patch")
    logger.info(f"Updated: {game['name']}")


def add_item_to_notion_database(game, achievements_info, review_text, steam_store_data):
    """创建新条目"""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    playtime = round(float(game["playtime_forever"]) / 60, 1)
    last_played_time = None
    if game.get("rtime_last_played", 0) > 0:
        last_played_time = time.strftime("%Y-%m-%d", time.localtime(game["rtime_last_played"]))

    store_url = f"https://store.steampowered.com/app/{game['appid']}"
    icon_url = f"https://media.steampowered.com/steamcommunity/public/images/apps/{game['appid']}/{game['img_icon_url']}.jpg"
    cover_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{game['appid']}/header.jpg"

    total = achievements_info["total"]
    achieved = achievements_info["achieved"]
    completion = round((achieved / total) * 100, 1) if total > 0 else -1

    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "name": {"title": [{"type": "text", "text": {"content": game["name"]}}]},
            "playtime": {"number": playtime},
            "last play": {"date": {"start": last_played_time} if last_played_time else None},
            "store url": {"url": store_url},
            "completion": {"number": completion},
            "total achievements": {"number": total},
            "achieved achievements": {"number": achieved},
            "review": {"rich_text": [{"type": "text", "text": {"content": review_text}}]},
            "info": {"rich_text": [{"type": "text", "text": {"content": steam_store_data["info"]}}]},
            "tags": {"multi_select": steam_store_data["tag"]},
        },
        "cover": {"type": "external", "external": {"url": cover_url}},
        "icon": {"type": "external", "external": {"url": icon_url}},
    }
    send_request_with_retry(url, headers=headers, json_data=data, method="post")
    logger.info(f"Added new: {game['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="启用调试日志输出")
    args = parser.parse_args()

    logger = logging.getLogger("")
    logger.setLevel(logging.INFO)
    for h in logger.handlers[:]:
        logger.removeHandler(h)

    if args.debug:
        fh = logging.FileHandler("app.log", encoding="utf-8")
        fh.setLevel(logging.INFO)
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        logger.addHandler(ch)

    owned_game_data = get_owned_game_data_from_steam()
    if not owned_game_data:
        logger.error("无法从 Steam 获取数据")
        exit(1)

    for game in owned_game_data["response"]["games"]:
        achievements_info = get_achievements_count(game)
        review_text = get_steam_review_info(game["appid"], STEAM_USER_ID)
        steam_store_data = get_steam_store_info(game["appid"])

        queryed_item = query_item_from_notion_database(game)
        if queryed_item.get("results"):
            page_id = queryed_item["results"][0]["id"]
            update_item_to_notion_database(page_id, game, achievements_info, review_text, steam_store_data)
        else:
            add_item_to_notion_database(game, achievements_info, review_text, steam_store_data)
