import json
import os
import sys
import requests

# 設定檔
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
DATA_FILE = "last_news.json"

# Komoe 繁中版賽馬娘官方 API 結構
NEWS_PAGE_URL = "https://uma.komoejoy.com/news?t=all"
API_URL = "https://uma.komoejoy.com/api/news/list"


def load_sent_history():
    """載入已發送過的新聞 ID 紀錄"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[Warn] 讀取紀錄檔失敗: {e}")
            return set()
    return set()


def save_sent_history(sent_set):
    """儲存最新發送紀錄"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_set), f, ensure_ascii=False, indent=2)


def fetch_latest_news():
    """模擬官網前端請求，獲取最新新聞列表"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://uma.komoejoy.com/news?t=all",
        "Origin": "https://uma.komoejoy.com",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8"
    }

    news_list = []

    # 方式 A：GET 請求
    try:
        print("[Info] 嘗試以 GET 方式請求官方 API...")
        res = requests.get(
            API_URL, 
            params={"page": 1, "pageSize": 10, "type": "all", "t": "all"}, 
            headers=headers, 
            timeout=15
        )
        print(f"[Info] GET 回應碼: {res.status_code}")
        
        if res.status_code == 200:
            data = res.json()
            items = data.get("data", {})
            if isinstance(items, dict):
                items = items.get("list", [])
            elif not isinstance(items, list):
                items = []

            for item in items:
                news_id = str(item.get("id") or item.get("news_id") or "")
                if not news_id:
                    continue
                title = item.get("title", "無標題")
                category = item.get("category_name") or item.get("type_name") or "遊戲"
                publish_time = item.get("publish_time") or item.get("date") or ""
                cover_image = item.get("cover") or item.get("image") or ""
                link = f"https://uma.komoejoy.com/news_detail.html?id={news_id}"

                news_list.append({
                    "id": news_id,
                    "title": title,
                    "category": category,
                    "date": publish_time,
                    "link": link,
                    "image": cover_image
                })

            if news_list:
                print(f"[Success] GET 方式成功獲取 {len(news_list)} 則公告！")
                return news_list
    except Exception as e:
        print(f"[Warn] GET 請求異常: {e}")

    # 方式 B：POST 備援（部分 Komoe 網站前端採用 POST payload）
    try:
        print("[Info] 切換至 POST 方式請求 API...")
        payload = {"page": 1, "pageSize": 10, "type": "all"}
        res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        print(f"[Info] POST 回應碼: {res.status_code}")
        
        if res.status_code == 200:
            data = res.json()
            items = data.get("data", {})
            if isinstance(items, dict):
                items = items.get("list", [])
            elif not isinstance(items, list):
                items = []

            for item in items:
                news_id = str(item.get("id") or item.get("news_id") or "")
                if not news_id:
                    continue
                title = item.get("title", "無標題")
                category = item.get("category_name") or item.get("type_name") or "遊戲"
                publish_time = item.get("publish_time") or item.get("date") or ""
                cover_image = item.get("cover") or item.get("image") or ""
                link = f"https://uma.komoejoy.com/news_detail.html?id={news_id}"

                news_list.append({
                    "id": news_id,
                    "title": title,
                    "category": category,
                    "date": publish_time,
                    "link": link,
                    "image": cover_image
                })

            if news_list:
                print(f"[Success] POST 方式成功獲取 {len(news_list)} 則公告！")
                return news_list
    except Exception as e:
        print(f"[Warn] POST 請求異常: {e}")

    return news_list


def send_discord_webhook(news):
    """傳送 Discord Rich Embed 卡片"""
    embed = {
        "title": news["title"],
        "url": news["link"],
        "color": 15822180,  # 賽馬娘官方粉色
        "author": {
            "name": f"【{news['category']}】賽馬娘 Pretty Derby 最新公告",
            "icon_url": "https://uma.komoejoy.com/favicon.ico"
        },
        "fields": [],
        "footer": {
            "text": "賽馬娘繁中版公告自動推播"
        }
    }

    if news["date"]:
        embed["fields"].append({
            "name": "📅 發布時間",
            "value": news["date"],
            "inline": True
        })

    if news["image"]:
        embed["image"] = {"url": news["image"]}

    payload = {"embeds": [embed]}

    res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    if res.status_code in [200, 204]:
        print(f"[Success] 已成功推播至 DC: [{news['category']}] {news['title']}")
        return True
    else:
        print(f"[Error] Webhook 推播失敗 ({res.status_code}): {res.text}")
        return False


def main():
    if not WEBHOOK_URL:
        print("[Fatal] 未偵測到 DISCORD_WEBHOOK 環境變數。")
        sys.exit(1)

    sent_history = load_sent_history()
    news_list = fetch_latest_news()

    if not news_list:
        print("[Warn] 暫時無法獲取官方公告，程式正常結束。")
        return  # 💡 改為 return 正常結束，避免 Actions 報錯標紅

    new_posts_found = False

    # 從舊到新推播
    for news in reversed(news_list):
        if news["id"] not in sent_history:
            if send_discord_webhook(news):
                sent_history.add(news["id"])
                new_posts_found = True

    if new_posts_found:
        save_sent_history(sent_history)
        print("[Info] 已更新 last_news.json 歷史紀錄。")
    else:
        print("[Info] 沒有發現新公告。")


if __name__ == "__main__":
    main()
