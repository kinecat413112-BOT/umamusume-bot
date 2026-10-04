import json
import os
import sys
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
DATA_FILE = "last_news.json"
TARGET_URL = "https://uma.komoejoy.com/news?t=all"

# 設定您提供的圖床連結做為備用圖片
DEFAULT_IMAGE_URL = "https://duk.tw/Xaqm2l/p"


def load_sent_history():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return set()
                return set(json.loads(content))
        except json.JSONDecodeError:
            print("[Info] 歷史紀錄檔為空或格式不符，將建立新的紀錄。")
            return set()
        except Exception as e:
            print(f"[Warn] 讀取紀錄檔失敗: {e}")
            return set()
    return set()


def save_sent_history(sent_set):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_set), f, ensure_ascii=False, indent=2)


def clean_content_text(text, pure_title):
    """清理多餘空行、網頁導覽雜訊並平滑斷句"""
    if not text:
        return ""

    noise_patterns = [
        r"News", r"最新消息", r"Top", r"遊戲", 
        r"詳情請點擊此處", r"遊戲\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}"
    ]
    
    lines = text.splitlines()
    clean_lines = []
    
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        if any(re.search(pattern, line_str, re.IGNORECASE) for pattern in noise_patterns):
            continue
        if line_str == pure_title or pure_title in line_str:
            continue
        clean_lines.append(line_str)

    return "\n".join(clean_lines).strip()


def fetch_latest_news_with_playwright():
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

            soup = BeautifulSoup(page.content(), "html.parser")
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
                    
                    # 剝離日期前綴與「詳情請點擊此處」
                    pure_title = re.sub(r"^遊戲\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}\s*", "", text)
                    pure_title = pure_title.replace("詳情請點擊此處", "").strip()

                    # 抓取列表預覽圖
                    img_tag = a.find("img") or a.parent.find("img")
                    image_url = ""
                    if img_tag and img_tag.get("src"):
                        src = img_tag["src"]
                        image_url = src if src.startswith("http") else f"https://uma.komoejoy.com{src}"

                    news_list.append({
                        "id": str(news_id),
                        "title": pure_title if pure_title else text,
                        "link": link,
                        "image": image_url,
                        "description": ""
                    })

            print(f"[Success] 成功解析出 {len(news_list)} 則公告！")

            # 進入內頁抓取摘要與圖片
            for news in news_list[:5]:
                try:
                    detail_page = context.new_page()
                    detail_page.goto(news["link"], wait_until="networkidle", timeout=15000)
                    detail_page.wait_for_timeout(3500)
                    
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    detail_page.close()

                    for s in detail_soup(["script", "style"]):
                        s.extract()

                    article_body = (
                        detail_soup.find("article") or
                        detail_soup.find("div", class_=re.compile(r"content|detail|article|news|main|text|p-", re.I)) or
                        detail_soup.find("main") or
                        detail_soup.body
                    )

                    if article_body:
                        raw_text = article_body.get_text(separator="\n")
                        cleaned = clean_content_text(raw_text, news["title"])
                        
                        if len(cleaned) > 150:
                            news["description"] = cleaned[:147] + "..."
                        else:
                            news["description"] = cleaned

                    # 嘗試抓取內頁公告的專屬圖片，若沒有則套用指定的圖片網址
                    if not news["image"]:
                        content_img = detail_soup.find("img")
                        if content_img and content_img.get("src"):
                            src = content_img["src"]
                            news["image"] = src if src.startswith("http") else f"https://uma.komoejoy.com{src}"
                        else:
                            news["image"] = DEFAULT_IMAGE_URL

                except Exception as err:
                    print(f"[Warn] 內頁摘要抓取跳過 ({news['id']}): {err}")
                    if not news["image"]:
                        news["image"] = DEFAULT_IMAGE_URL

        except Exception as e:
            print(f"[Error] Playwright 執行失敗: {e}")
        finally:
            browser.close()

    return news_list


def send_discord_webhook(news):
    """發送 Discord 推播"""

    # 確保無圖片時必定帶入設定的圖床網址
    img_url = news.get("image") if news.get("image") else DEFAULT_IMAGE_URL

    embed = {
        "title": news["title"],
        "url": news["link"],
        "color": 15822180,       # 賽馬娘粉色
        "image": {"url": img_url}
    }

    if news.get("description"):
        embed["description"] = news["description"]
    else:
        embed["description"] = "點擊標題查看詳細公告..."

    payload = {
        "content": news["title"],
        "embeds": [embed]
    }

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
