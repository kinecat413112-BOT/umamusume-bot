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
    """使用 Playwright 解析新聞列表，並點進內頁抓取內文摘要與封面圖"""
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
            print(f"[Info] 前往官網列表: {TARGET_URL}")
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
                    
                    # 抓取列表預覽圖（若有）
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
                        "image": image_url,
                        "description": ""  # 預留內文摘要欄位
                    })

            print(f"[Success] 成功解析出 {len(news_list)} 則公告！")

            # 針對最新 5 則公告，點進內頁抓取「詳細內文」與「封面圖」
            for news in news_list[:5]:
                try:
                    print(f"[Info] 前往內頁抓取詳細內容: {news['id']}")
                    detail_page = context.new_page()
                    detail_page.goto(news["link"], wait_until="domcontentloaded", timeout=15000)
                    detail_page.wait_for_timeout(1500)
                    
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    detail_page.close()

                    # 1. 抓取內文文字摘要
                    # 移除腳本與樣式標籤
                    for script in detail_soup(["script", "style"]):
                        script.extract()

                    # 尋找文章內容容器（針對常見內文區塊）
                    content_block = (
                        detail_soup.find("div", class_="article-content") or 
                        detail_soup.find("div", class_="news-detail") or
                        detail_soup.find("div", class_="content") or
                        detail_soup.body
                    )

                    if content_block:
                        lines = [line.strip() for line in content_block.get_text(separator="\n").splitlines() if line.strip()]
                        # 排除標題重複部分
                        filtered_lines = [line for line in lines if line != news["title"]]
                        full_text = "\n".join(filtered_lines)
                        
                        # 限制長度防爆字數（Discord limit: 2048 字，限制在 500 字以內摘要）
                        if len(full_text) > 500:
                            news["description"] = full_text[:497] + "..."
                        else:
                            news["description"] = full_text

                    # 2. 若列表沒圖，補抓內頁第一張 Banner 大圖
                    if not news["image"]:
                        content_img = detail_soup.find("img")
                        if content_img and content_img.get("src"):
                            src = content_img["src"]
                            news["image"] = src if src.startswith("http") else f"https://uma.komoejoy.com{src}"

                except Exception as err:
                    print(f"[Warn] 內頁內容抓取失敗/跳過 ({news['id']}): {err}")

        except Exception as e:
            print(f"[Error] Playwright 執行失敗: {e}")
        finally:
            browser.close()

    return news_list


def send_discord_webhook(news):
    """傳送包含內文摘要與圖片的 Discord 卡片"""
    embed = {
        "title": news["title"],
        "url": news["link"],
        "color": 15822180,  # 賽馬娘官方粉色
        "author": {
            "name": f"【{news['category']}】賽馬娘 Pretty Derby"
        }
    }

    # 加入內文摘要 (description)
    if news.get("description"):
        embed["description"] = news["description"]

    # 附上內頁 Banner 圖片
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
