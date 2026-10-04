import json
import os
import sys
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
DATA_FILE = "last_news.json"
TARGET_URL = "https://uma.komoejoy.com/news?t=all"


def load_sent_history():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[Warn] 讀取紀錄檔失敗: {e}")
            return set()
    return set()


def save_sent_history(sent_set):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_set), f, ensure_ascii=False, indent=2)


def fetch_latest_news_with_playwright():
    """使用 Playwright 模擬真實瀏覽器解析新聞列表與圖片"""
    news_list = []
    
    with sync_playwright() as p:
        print("[Info] 啟動 Playwright 瀏覽器...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-TW"
        )
        page = context.new_page()

        try:
            print(f"[Info] 前往官網: {TARGET_URL}")
            page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            anchors = soup.find_all("a")
            seen_ids = set()

            for a in anchors:
                href = a.get("href", "")
                text = a.get_text(strip=True)

                if "id=" in href or "detail" in href:
                    news_id = href.split("id=")[-1] if "id=" in href else href
                    if news_id in seen_ids or not text:
                        continue
                    seen_ids.add(news_id)

                    link = href if href.startswith("http") else f"https://uma.komoejoy.com{href}"
                    
                    # 抓取列表預覽圖
                    img_tag = a.find("img") or a.parent.find("img")
                    image_url = ""
                    if img_tag and img_tag.get("src"):
                        src = img_tag["src"]
                        image_url = src if src.startswith("http") else f"https://uma.komoejoy.com{src}"

                    news_list.append({
                        "id": str(news_id),
                        "title": text,
                        "category": "遊戲公告",
                        "link": link,
                        "image": image_url
                    })

            print(f"[Success] 成功解析出 {len(news_list)} 則公告！")

            # 針對最新 3 則公告，若無預覽圖則進入內頁抓取
            for news in news_list[:3]:
                if not news["image"]:
                    try:
                        print(f"[Info] 前往內頁補抓圖片: {news['id']}")
                        detail_page = context.new_page()
                        detail_page.goto(news["link"], wait_until="domcontentloaded", timeout=10000)
                        detail_page.wait_for_timeout(1000)
                        
                        detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                        detail_page.close()

                        content_img = detail_soup.find("img")
                        if content_img and content_img.get("src"):
                            src = content_img["src"]
                            news["image"] = src if src.startswith("http") else f"https://uma.komoejoy.com{src}"
                    except Exception as err:
                        print(f"[Warn] 內頁圖片抓取跳過 ({news['id']}): {err}")

        except Exception as e:
            print(f"[Error] Playwright 執行失敗: {e}")
        finally:
            browser.close()

    return news_list


def send_discord_webhook(news):
    """傳送乾淨無破圖的 Discord 卡片"""
    embed = {
        "title": news["title"],
        "url": news["link"],
        "color": 15822180,  # 賽馬娘官方粉色
        "author": {
            "name": f"【{news['category']}】賽馬娘 Pretty Derby"
        }
    }

    if news.get("image"):
        embed["image"] = {"url": news["image"]}

    payload = {"embeds": [embed]}

    res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    if res.status_code in [200, 204]:
        print(f"[Success] 已推播至 DC: {news['title']}")
        return True
    else:
        print(f"[Error] Webhook 推播失敗 ({res.status_code}): {res.text}")
        return False


def main():
    if not WEBHOOK_URL:
        print("[Fatal] 未設定 DISCORD_WEBHOOK 環境變數。")
        sys.exit(1)

    sent_history = load_sent_history()
    news_list = fetch_latest_news_with_playwright()

    if not news_list:
        print("[Warn] 仍未抓取到任何資料，結束執行。")
        return

    new_posts_found = False

    # 從舊到新順序推播
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
