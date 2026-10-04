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

# 備用圖片：僅在官網內文完全沒有圖片（或只有 Loading 圖）時使用
DEFAULT_IMAGE_URL = "https://i.postimg.cc/7Lm5Djnr/1b74775aa80028684f67edf5e2432f38743f648f313513a1b24813b41074eb8a.jpg"


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
                    
                    pure_title = re.sub(r"^遊戲\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}\s*", "", text)
                    pure_title = pure_title.replace("詳情請點擊此處", "").strip()

                    news_list.append({
                        "id": str(news_id),
                        "title": pure_title if pure_title else text,
                        "link": link,
                        "image": "",
                        "description": ""
                    })

            print(f"[Success] 成功解析出 {len(news_list)} 則公告！")

            # 進入內頁抓取摘要與圖片
            for news in news_list[:5]:
                try:
                    detail_page = context.new_page()
                    detail_page.goto(news["link"], wait_until="networkidle", timeout=20000)
                    
                    # 多等待 3 秒讓 Loading 遮罩消失與文章內容載入完成
                    detail_page.wait_for_timeout(3000)

                    # 1. 抓取文字摘要
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    for s in detail_soup(["script", "style", "nav", "header", "footer"]):
                        s.extract()

                    article_body = (
                        detail_soup.find("article") or
                        detail_soup.find("div", class_=re.compile(r"detail|content|article|news|main", re.I)) or
                        detail_soup.body
                    )

                    if article_body:
                        raw_text = article_body.get_text(separator="\n")
                        cleaned = clean_content_text(raw_text, news["title"])
                        news["description"] = cleaned[:147] + "..." if len(cleaned) > 150 else cleaned

                    # 2. 由 Playwright DOM 提取所有 img 屬性
                    img_srcs = detail_page.evaluate("""
                        () => {
                            const imgs = Array.from(document.querySelectorAll('img'));
                            return imgs.map(img => img.src || img.getAttribute('data-src') || '').filter(Boolean);
                        }
                    """)

                    detail_page.close()

                    # 篩選出真正的公告 Banner 圖（徹底排除 Loading 及選單圖）
                    found_img = ""
                    ignore_keywords = [
                        "logo", "icon", "nav", "btn", "share", "avatar", 
                        "footer", "header", "favicon", "loading", "loader", "load"
                    ]

                    for src in img_srcs:
                        src_lower = src.lower()
                        
                        # 1. 排除關鍵字（含有 loading、logo、icon 等）
                        if any(k in src_lower for k in ignore_keywords):
                            continue
                        
                        # 2. 排除 GIF 動圖（因為 Loading 圖皆為 GIF）
                        if src_lower.endswith(".gif") or ".gif?" in src_lower:
                            continue

                        # 符合非 GIF 的靜態大圖（.png / .jpg / .jpeg / .webp）即採用
                        found_img = src
                        break

                    news["image"] = found_img
                    print(f"[Debug] 公告 [{news['title'][:10]}] 抓到圖片 ➔ {found_img if found_img else '無圖片 (套用預設大圖)'}")

                except Exception as err:
                    print(f"[Warn] 內頁摘要/圖片抓取跳過 ({news['id']}): {err}")

        except Exception as e:
            print(f"[Error] Playwright 執行失敗: {e}")
        finally:
            browser.close()

    return news_list


def send_discord_webhook(news):
    """發送 Discord 推播"""

    img_url = news.get("image", "").strip()
    if not img_url or not img_url.startswith("http"):
        img_url = DEFAULT_IMAGE_URL

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
        print(f"[Success] 已推播至 DC: {news['title']} (圖片: {img_url})")
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
