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

# 備用圖片：當列表頁面的卡片本身完全沒有圖片時才使用
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

            # 在列表頁精準抓取卡片與對應的 Banner 圖片
            cards_data = page.evaluate("""
                () => {
                    const results = [];
                    // 取得所有帶有 id 或 detail 的公告連結
                    const links = Array.from(document.querySelectorAll('a[href*="detail"], a[href*="id="]'));
                    
                    links.forEach(a => {
                        const href = a.getAttribute('href');
                        // 找到目前卡片最外層容器
                        let container = a;
                        for (let i = 0; i < 4; i++) {
                            if (container.parentElement && container.tagName !== 'LI' && container.tagName !== 'BODY') {
                                container = container.parentElement;
                            }
                        }

                        let foundImg = '';

                        // 1. 尋找卡片內的 img 標籤
                        const imgs = Array.from(container.querySelectorAll('img'));
                        for (let img of imgs) {
                            let src = img.src || img.getAttribute('data-src') || img.getAttribute('data-original') || '';
                            if (src && !src.includes('logo') && !src.includes('favicon') && !src.includes('nav')) {
                                foundImg = src;
                                break;
                            }
                        }

                        // 2. 如果沒有 img，尋找 CSS background-image
                        if (!foundImg) {
                            const bgElems = Array.from(container.querySelectorAll('*'));
                            for (let elem of bgElems) {
                                const style = window.getComputedStyle(elem);
                                const bgImg = style.backgroundImage;
                                if (bgImg && bgImg !== 'none' && bgImg.includes('url')) {
                                    const match = bgImg.match(/url\\(["']?(.*?)["']?\\)/);
                                    if (match && match[1]) {
                                        let url = match[1];
                                        if (!url.includes('logo') && !url.includes('favicon')) {
                                            foundImg = url;
                                            break;
                                        }
                                    }
                                }
                            }
                        }

                        const text = a.innerText || container.innerText;

                        if (href) {
                            results.push({
                                href: href,
                                text: text,
                                card_img: foundImg
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
                
                # 整理標題
                pure_title = re.sub(r"^遊戲\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}\s*", "", text)
                pure_title = pure_title.replace("詳情請點擊此處", "").strip()

                # 補全完整圖片網址
                final_img = ""
                if card_img:
                    if card_img.startswith("http"):
                        final_img = card_img
                    elif card_img.startswith("//"):
                        final_img = f"https:{card_img}"
                    else:
                        final_img = f"https://uma.komoejoy.com{card_img if card_img.startswith('/') else '/' + card_img}"

                news_list.append({
                    "id": str(news_id),
                    "title": pure_title if pure_title else text,
                    "link": link,
                    "image": final_img,
                    "description": ""
                })

            print(f"[Success] 成功解析出 {len(news_list)} 則公告！")

            # 進入內頁「只抓取文字」，不觸碰圖片
            for news in news_list[:5]:
                try:
                    detail_page = context.new_page()
                    detail_page.goto(news["link"], wait_until="domcontentloaded", timeout=15000)
                    detail_page.wait_for_timeout(2000)
                    
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    detail_page.close()

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

                    print(f"[Debug] 公告 [{news['title'][:12]}...] 列表卡片圖片 ➔ {news['image'] if news['image'] else '無圖片 (套用預設大圖)'}")

                except Exception as err:
                    print(f"[Warn] 內頁摘要抓取跳過 ({news['id']}): {err}")

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
