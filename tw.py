import json
import os
import sys
import requests
from bs4 import BeautifulSoup

# 設定檔
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
DATA_FILE = "last_news.json"

# 賽馬娘繁中版官方新聞網址與 API
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
    """爬取最新公告列表 (優先透過 API，失敗則回退至 HTML 解析)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": NEWS_PAGE_URL,
        "Accept": "application/json, text/plain, */*"
    }

    # 嘗試策略 1：呼叫官網 API
    try:
        response = requests.get(API_URL, params={"page": 1, "pageSize": 10, "type": "all"}, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            items = data.get("data", {})
            if isinstance(items, dict):
                items = items.get("list", [])
            
            if items:
                news_list = []
                for item in items:
                    news_id = str(item.get("id") or item.get("news_id"))
                    title = item.get("title", "無標題")
                    category = item.get("category_name", "遊戲")
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
                return news_list
    except Exception as e:
        print(f"[Info] API 讀取未順利完成，切換至 HTML 解析模式: {e}")

    # 嘗試策略 2：HTML 靜態解析備援
    try:
        response = requests.get(NEWS_PAGE_URL, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        news_list = []
        for item in soup.select(".news-list-item, .news-item"):
            link_tag = item.select_one("a") or item
            link = link_tag.get("href", "")
            news_id = link.split("id=")[-1] if "id=" in link else link
            title = item.select_one(".title").get_text(strip=True) if item.select_one(".title") else "無標題"
            category = item.select_one(".category, .tag").get_text(strip=True) if item.select_one(".category, .tag") else "遊戲"
            
            if news_id and link:
                if not link.startswith("http"):
                    link = f"https://uma.komoejoy.com{link}"
                news_list.append({
                    "id": str(news_id),
                    "title": title,
                    "category": category,
                    "date": "",
                    "link": link,
                    "image": ""
                })
        return news_list
    except Exception as e:
        print(f"[Error] HTML 解析失敗: {e}")
        return []


def send_discord_webhook(news):
    """傳送 Discord Rich Embed 卡片"""
    embed = {
        "title": news["title"],
        "url": news["link"],
        "color": 15822180,  # 賽馬娘官方粉色 (#F170A4)
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
        print(f"[Success] 已推播: [{news['category']}] {news['title']}")
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
        print("[Info] 未獲取到任何公告。")
        return

    new_posts_found = False

    # 反轉列表順序（從舊到新推播），確保 Discord 頻道內訊息排序正確
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
