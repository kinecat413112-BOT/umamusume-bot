def send_discord_webhook(news):
    """傳送乾淨精簡的 Discord 卡片"""
    embed = {
        "title": news["title"],
        "url": news["link"],
        "color": 15822180,  # 賽馬娘官方粉色
        "author": {
            "name": f"【{news['category']}】賽馬娘 Pretty Derby"
        }
    }

    # 如果有封面圖則附上
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
