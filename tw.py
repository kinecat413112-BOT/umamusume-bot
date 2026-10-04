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

# 備用圖片：僅在官網內頁完全沒有圖片時使用
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


def extract_best_image(soup):
    """全頁掃描圖片標籤與 CSS 背景圖，找出最可能是公告 Banner 的圖片"""
    
    # 移除導覽與頁尾，避免抓到選單圖
    for s in soup(["nav", "header", "footer", "script", "style"]):
        s.extract()

    candidate_imgs = []

    # 1. 抓取所有 <img> 標籤（含 src, data-src, lazy-src 等）
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        src = src.strip()
        if src:
            candidate_imgs.append(src)

    # 2. 抓取帶有 style="background-image: url(...)" 的元素
    for elem in soup.find_all(style=True):
        style = elem["style"]
        match = re.search(r'url\(([\'"]?)(.*?)\1\)', style, re.I)
        if match:
            candidate_imgs.append(match.group(2))

    # 過濾並選出第一張合格的公告圖片
    ignore_keywords = [
        "logo", "icon", "nav", "btn", "share", "avatar", "footer", 
        "header", "favicon", "bg_site", "common", "p-news__tab"
    ]

    for raw_url in candidate_imgs:
        url_lower = raw_url.lower()
        
        # 排除包含選單、圖示關鍵字的圖片
        if any(k in url_lower for k in ignore_keywords):
            continue

        # 補全完整 HTTP 網址
        if raw_url.startswith("http"):
            full_url = raw_url
        elif raw_url.startswith("//"):
            full_url = f"https:{raw_url}"
        else:
            full_url = f"https://uma.komoejoy.com{raw_url if raw_url.startswith('/') else '/' + raw_url}"

        # 只要是包含圖片副檔名或含有 cms/news/upload/image 等資源目錄的即視為有效 Banner
        if any(ext in url_lower for ext in [".jpg", ".png", ".jpeg", ".webp"]) or "upload" in url_lower or "cms" in url_lower:
            return full_url

    return ""


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
                    # 提高超時並等待 networkidle 確保動態圖片載入完畢
                    detail_page.goto(news["link"], wait_until="networkidle", timeout=20000)
                    detail_page.wait_for_timeout(4000)
                    
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    detail_page.close()

                    # 1. 抓取文字摘要
                    article_body = (
                        detail_soup.find("article") or
                        detail_soup.find("div", class_=re.compile(r"detail|content|article|news|main", re.I)) or
                        detail_soup.body
                    )

                    if article_body:
                        raw_text = article_body.get_text(separator="\n")
                        cleaned = clean_content_text(raw_text, news["title"])
                        news["description"] = cleaned[:147] + "..." if len(cleaned) > 150 else cleaned

                    # 2. 精準提取圖片 (全頁分析)
                    found_img = extract_best_image(detail_soup)
                    news["image"] = found_img
                    print(f"[Debug] 公告 [{news['title'][:10]}...] 解析圖片網址 ➔ {found_img if found_img else '未抓到 (將使用預設圖)'}")

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
        print(f"[Success] 已推播至 DC: {news['title']} (使用圖片: {img_url})")
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
