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

# 備用圖片：當列表頁面的卡片本身沒有圖片時使用
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

            # 1. 在列表頁面直接分析 DOM 卡片元素
            # 透過 Playwright 抓取列表頁的所有新聞區塊及其內部的 img 圖片與連結
            cards_data = page.evaluate("""
                () => {
                    const results = [];
                    // 尋找列表頁上的新聞連結
                    const links = Array.from(document.querySelectorAll('a[href*="detail"], a[href*="id="]'));
                    
                    links.forEach(a => {
                        const href = a.getAttribute('href');
                        // 找到該卡片區塊容器 (向上尋找最近的卡片或父級容器)
                        const container = a.closest('li') || a.closest('div') || a;
                        // 檢查卡片內部是否有 Banner 圖片
                        const img = container.querySelector('img');
                        const imgSrc = img ? (img.src || img.getAttribute('data-src') || '') : '';
                        const text = a.innerText || container.innerText;

                        if (href) {
                            results.push({
                                href: href,
                                text: text,
                                card_img: imgSrc
                            });
                        }
                    });
                    return results;
                }
            """)

            seen_ids = set()

            for item in cards_data:
                href = item["href"]
                text = item["text"]
                card_img = item["card_img"]

                news_id = href.split("id=")[-1] if "id=" in href else href
                if news_id in seen_ids or not text:
                    continue
                seen_ids.add(news_id)

                link = href if href.startswith("http") else f"https://uma.komoejoy.com{href}"
                
                # 整理乾淨的標題
                pure_title = re.sub(r"^遊戲\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}\s*", "", text)
                pure_title = pure_title.replace("詳情請點擊此處", "").strip()

                # 排除非 Banner 的系統雜圖 (如 logo, icon 等)
                final_card_img = ""
                if card_img and not any(k in card_img.lower() for k in ["logo", "icon", "nav", "loading", "btn"]):
                    final_card_img = card_img

                news_list.append({
                    "id": str(news_id),
                    "title": pure_title if pure_title else text,
                    "link": link,
                    "image": final_card_img, # 這是列表頁抓到的 Banner
                    "description": ""
                })

            print(f"[Success] 成功從列表頁解析出 {len(news_list)} 則公告！")

            # 2. 進入內頁「只抓取文字」，完全不抓圖片
            for news in news_list[:5]:
                try:
                    detail_page = context.new_page()
                    detail_page.goto(news["link"], wait_until="domcontentloaded", timeout=15000)
                    detail_page.wait_for_timeout(2000)
                    
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    detail_page.close()

                    # 清除干擾標籤
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

                    print(f"[Debug] 公告 [{news['title'][:10]}] 列表卡片圖片 ➔ {news['image'] if news['image'] else '無圖片 (將使用預設圖)'}")

                except Exception as err:
                    print(f"[Warn] 內頁摘要抓取跳過 ({news['id']}): {err}")

        except Exception as e:
            print(f"[Error] Playwright 執行失敗: {e}")
        finally:
            browser.close()

    return news_list


def send_discord_webhook(news):
    """發送 Discord 推播"""

    # 如果列表卡片有圖就用卡片的圖，沒圖則自動帶入預設圖片
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
