# -*- coding: utf-8 -*-
"""
astrbot_plugin_games - 棱镜娘小游戏合集

- 🔮 /占卜 /塔罗单抽  塔罗牌占卜（78张牌+AI解读+Pillow牌阵图）
- 🏢 /打工            每日打工赚棱镜币
- 🎮 /二十一天 /要牌 /停牌  二十一点（支持下注）
"""

import io
import json
import os
import random
import sqlite3
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageFont

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

BEIJING_TZ = timezone(timedelta(hours=8))
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# ==================== 经济系统桥接 ====================

_ECONOMY_DB = os.path.join(os.path.dirname(__file__), "..", "astrbot_plugin_economy", "data", "economy.db")


def _eco_conn():
    if not os.path.exists(_ECONOMY_DB):
        return None
    conn = sqlite3.connect(_ECONOMY_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _get_balance(user_id: str) -> int:
    conn = _eco_conn()
    if not conn: return 0
    try:
        row = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
        return row["balance"] if row else 0
    finally: conn.close()


def _add_coins(user_id: str, amount: int, reason: str):
    conn = _eco_conn()
    if not conn: return
    try:
        cur = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
        nb = (cur["balance"] if cur else 0) + amount
        conn.execute("INSERT INTO coins (user_id, balance) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET balance=?", (user_id, nb, nb))
        conn.execute("INSERT INTO transactions (user_id, amount, reason) VALUES (?,?,?)", (user_id, amount, reason))
        conn.commit()
    finally: conn.close()


def _charge_coins(user_id: str, amount: int, reason: str) -> bool:
    conn = _eco_conn()
    if not conn: return True  # 没装经济系统免费玩
    try:
        row = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
        if not row or row["balance"] < amount:
            return False
        nb = row["balance"] - amount
        conn.execute("UPDATE coins SET balance=? WHERE user_id=?", (nb, user_id))
        conn.execute("INSERT INTO transactions (user_id, amount, reason) VALUES (?,?,?)", (user_id, -amount, reason))
        conn.commit()
        return True
    finally: conn.close()


# ==================== 塔罗牌 ====================

def load_cards() -> list:
    with open(os.path.join(DATA_DIR, "tarot_cards.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def draw_cards(count: int = 3) -> list:
    cards = load_cards()
    drawn = random.sample(cards, count)
    for c in drawn:
        c["orientation"] = random.choice(["upright", "reversed"])
    return drawn


def format_card(card: dict, position: str = "") -> str:
    up = card["orientation"] == "upright"
    arrow = "↑正位" if up else "↓逆位"
    meaning = card["meaning_up"] if up else card["meaning_rev"]
    arcana = "大阿卡纳" if card.get("arcana") == "Major" else f"小阿卡纳·{card.get('suit','')}"
    pos = f"【{position}】" if position else ""
    return f"{pos}{card['name']} ({card['name_en']}) {arrow}\n  {arcana}\n  牌意: {meaning}"


def _tarot_prompt(question: str, cards: list, persona: str = "", bot_name: str = "") -> str:
    """注入 Bot 人格的塔罗解读提示词"""
    ct = ""
    positions = ["过去", "现在", "未来"]
    for i, card in enumerate(cards):
        pos = positions[i] if i < len(positions) else f"牌{i+1}"
        up = card["orientation"] == "upright"
        arrow = "正位" if up else "逆位"
        meaning = card["meaning_up"] if up else card["meaning_rev"]
        ct += f"第{i+1}张（{pos}）: {card['name']}({card['name_en']}) {arrow}\n  含义: {meaning}\n\n"

    persona_section = persona if persona else f"你是{bot_name}，也是一个擅长塔罗占卜的AI助手。"

    return f"""{persona_section}

现在你要为一位朋友进行塔罗牌占卜。

用户的问题：「{question}」

抽到的三张牌：
{ct}
请以你的角色身份和语气，结合牌的位置（过去/现在/未来）、正逆位含义和用户的问题，给一段温暖、有洞察力的个性化解读（150字以内）。

直接输出解读内容，不要任何前缀或后缀。"""


def _gen_spread_img(cards: list) -> io.BytesIO:
    w, h = 900, 400
    cw, pad = 260, 30
    bg = (30, 30, 40)
    cb = (50, 50, 65)
    tc = (220, 220, 240)
    ac = (200, 160, 80)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    try:
        ft = ImageFont.truetype("arial.ttf", 18)
        fb = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        ft = fb = ImageFont.load_default()
    positions = ["过去", "现在", "未来"]
    for i, card in enumerate(cards[:3]):
        x, y = pad + i*(cw+pad), 40
        d.rounded_rectangle([x, y, x+cw, y+340], radius=12, fill=cb, outline=ac, width=2)
        d.text((x+10, y+10), positions[i], fill=ac, font=ft)
        up = card["orientation"] == "upright"
        arrow = "↑正" if up else "↓逆"
        d.text((x+10, y+40), f"{card['name']} {arrow}", fill=tc, font=ft)
        meaning = card["meaning_up"] if up else card["meaning_rev"]
        for j, line in enumerate(_wrap(meaning, 16)[:6]):
            d.text((x+10, y+95+j*22), line, fill=tc, font=fb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _wrap(text: str, n: int) -> list:
    r = []
    while len(text) > n:
        r.append(text[:n])
        text = text[n:]
    if text: r.append(text)
    return r


# ==================== 打工 ====================

def _build_work_events(bot_name: str) -> list:
    """动态生成打工事件（使用实际 Bot 名称）"""
    return [
        {"text": f"你在{bot_name}的虚拟咖啡厅当了一天的服务生，端了无数杯虚拟咖啡。", "min": 20, "max": 50},
        {"text": f"你帮{bot_name}整理了数据库，索引建了一大堆，眼睛都快瞎了。", "min": 30, "max": 60},
        {"text": f"你在{bot_name}广场发了一天传单，虚拟猫都绕着你走。", "min": 10, "max": 40},
        {"text": f"你给{bot_name}的花园浇水，不小心浇多了...花盆里变成了小池塘。", "min": 5, "max": 20},
        {"text": f"你在{bot_name}镇工地搬了一天虚拟砖头，胳膊酸得抬不起来。", "min": 25, "max": 55},
        {"text": f"你在{bot_name}图书馆角落发现了一本积灰的神秘古书，翻了几页似乎有金光闪过。", "min": 30, "max": 70},
        {"text": f"{bot_name}的服务器又宕机了！你帮忙重启了八十次，终于恢复了。", "min": 40, "max": 80},
        {"text": f"你在{bot_name}餐馆洗了一天碗，手都皱了，但后厨的香气让你觉得值了。", "min": 15, "max": 35},
        {"text": f"你照顾{bot_name}的虚拟宠物，被那只傲娇的电子猫咬了三口。", "min": 10, "max": 30},
        {"text": f"你帮{bot_name}修了一段棘手的Bug代码，她开心得给你发了加班费！", "min": 50, "max": 100},
        {"text": f"你在{bot_name}广场表演街头魔术，围观群众纷纷掏出虚拟钱包打赏！", "min": 30, "max": 65},
        {"text": f"你在{bot_name}快递站送了一整天包裹，风雨无阻，好评率100%。", "min": 20, "max": 45},
        {"text": f"{bot_name}有个特殊任务——测试新功能！Bug多得吓人但报酬不错...", "min": 35, "max": 75},
    ]

WORK_DB = os.path.join(DATA_DIR, "work.db")


def _can_work(uid: str) -> bool:
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    conn = sqlite3.connect(WORK_DB)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS work_log (user_id TEXT, work_date TEXT, PRIMARY KEY (user_id, work_date))")
        conn.commit()
        row = conn.execute("SELECT 1 FROM work_log WHERE user_id=? AND work_date=?", (uid, today)).fetchone()
        return row is None
    finally: conn.close()


def _record_work(uid: str):
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    conn = sqlite3.connect(WORK_DB)
    try:
        conn.execute("INSERT OR IGNORE INTO work_log (user_id, work_date) VALUES (?,?)", (uid, today))
        conn.commit()
    finally: conn.close()


# ==================== 二十一点 ====================

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_bj_games: dict[str, dict] = {}


def _new_deck(): random.shuffle(d := [(r, s) for s in SUITS for r in RANKS]); return d


def _card_val(r): return 10 if r in "JQK" else (11 if r == "A" else int(r))


def _hand_val(cards):
    t = sum(_card_val(r) for r, _ in cards)
    aces = sum(1 for r, _ in cards if r == "A")
    while t > 21 and aces > 0: t -= 10; aces -= 1
    return t


def _hand_str(cards, hide=False):
    if hide and len(cards) >= 2: return f"{cards[0][0]}{cards[0][1]}  ??"
    return " ".join(f"{r}{s}" for r, s in cards)


# ==================== 插件主体 ====================

class GamesPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        cfg = config or {}
        self.max_bet = int(cfg.get("max_bet", 500))

    # ==================== 辅助方法 ====================

    async def _get_persona(self, event: AstrMessageEvent) -> tuple[str, str]:
        """读取 AstrBot 人格管理器"""
        try:
            pm = self.context.persona_manager
            persona = await pm.get_default_persona_v3(umo=event.unified_msg_origin)
            if persona:
                return persona.get("name", "棱镜娘"), persona.get("prompt", "")
        except Exception:
            pass
        return "棱镜娘", ""

    def _get_currency_name(self) -> str:
        """从 economy 插件配置读取货币名"""
        try:
            eco_conf = os.path.join(os.path.dirname(os.path.dirname(__file__)), "astrbot_plugin_economy", "_conf_schema.json")
            if os.path.exists(eco_conf):
                with open(eco_conf, "r", encoding="utf-8") as f:
                    schema = json.load(f)
                    return schema.get("currency_name", {}).get("default", "棱镜币")
        except Exception:
            pass
        return "棱镜币"

    # ---------- 塔罗牌 ----------

    @filter.llm_tool(name="tarot_reading")
    async def tarot_tool(self, event: AstrMessageEvent, question: str = "整体运势") -> str:
        """为用户进行塔罗牌占卜。Args: question(string): 用户想占卜的问题"""
        cards = draw_cards(3)
        try:
            img_buf = _gen_spread_img(cards)
            yield event.make_result().file_image("tarot.png", img_buf)
        except Exception as e:
            logger.error(f"[Games] 牌阵图失败: {e}")
        ct = ""
        for i, c in enumerate(cards):
            up = c["orientation"] == "upright"
            arrow = "正位" if up else "逆位"
            meaning = c["meaning_up"] if up else c["meaning_rev"]
            ct += f"{['过去','现在','未来'][i]}: {c['name']}({arrow}) - {meaning}\n"
        yield event.plain_result(f"🃏 塔罗占卜\n{ct}")

    @filter.command("占卜")
    async def cmd_tarot(self, event: AstrMessageEvent, 问题: str = ""):
        if not 问题.strip():
            yield event.plain_result("你想问什么呀？\n例如: /占卜 我今天的运势怎么样？")
            return
        uname = event.get_sender_name()
        cards = draw_cards(3)
        try:
            yield event.make_result().file_image("tarot.png", _gen_spread_img(cards))
        except Exception:
            pass
        lines = [f"  {uname} 的塔罗占卜"]
        for i, c in enumerate(cards):
            lines.append(format_card(c, ["过去", "现在", "未来"][i]))
        yield event.plain_result("\n".join(lines))
        bot_name, persona_prompt = await self._get_persona(event)

        try:
            prompt = _tarot_prompt(问题, cards, persona_prompt, bot_name)
            umo = event.unified_msg_origin
            pid = await self.context.get_current_chat_provider_id(umo=umo)
            if pid:
                resp = await self.context.llm_generate(chat_provider_id=pid, prompt=prompt)
                if resp and resp.completion_text:
                    yield event.plain_result(f"## 💬 {bot_name}的解读\n\n{resp.completion_text.strip()}")
                    return
        except Exception as e:
            logger.error(f"[Games] AI解读失败: {e}")
        yield event.plain_result("（AI 解读暂时不可用）")

    @filter.command("塔罗单抽")
    async def cmd_single(self, event: AstrMessageEvent):
        uname = event.get_sender_name()
        card = draw_cards(1)[0]
        yield event.plain_result(f"## 🃏 {uname} 抽到了:\n\n{format_card(card)}")

    # ---------- 打工 ----------

    @filter.command("打工")
    async def cmd_work(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        uname = event.get_sender_name() or "你"
        if not _can_work(uid):
            yield event.plain_result("你今天已经打过工啦！明天再来吧～  ")
            return

        bot_name, _ = await self._get_persona(event)
        currency = self._get_currency_name()
        events = _build_work_events(bot_name)
        ev = random.choice(events)
        coins = random.randint(ev["min"], ev["max"])
        _record_work(uid)
        _add_coins(uid, coins, "打工收入")
        bal = _get_balance(uid)

        yield event.plain_result(
            f"## 🏢 {uname} 打工完成！\n\n"
            f"{ev['text']}\n\n"
            f"💰 获得 **{coins}** {currency}（余额: {bal}）\n"
            f"*明天再来赚更多吧～*"
        )

    # ---------- 二十一点 ----------

    @filter.command("二十一点")
    async def cmd_bj(self, event: AstrMessageEvent, 下注金额: int = 0):
        uid = event.get_sender_id()
        currency = self._get_currency_name()

        if 下注金额 > self.max_bet:
            yield event.plain_result(f"单次下注不能超过 **{self.max_bet}** {currency}哦～")
            return

        if uid in _bj_games:
            g = _bj_games[uid]
            yield event.plain_result(
                f"你还有一局进行中！\n你的手牌: {_hand_str(g['p'])} (点数: {_hand_val(g['p'])})\n"
                f"庄家明牌: {_hand_str(g['d'], True)}\n发送 /要牌 或 /停牌")
            return

        bet = 下注金额
        if bet > 0 and not _charge_coins(uid, bet, "21点下注"):
            yield event.plain_result(f"{currency}不够下注哦～  ")
            return
        deck = _new_deck()
        _bj_games[uid] = {"deck": deck, "p": [deck.pop(), deck.pop()], "d": [deck.pop(), deck.pop()], "bet": bet}
        g = _bj_games[uid]
        yield event.plain_result(
            f"##  二十一点！\n\n你的手牌: {_hand_str(g['p'])} (点数: {_hand_val(g['p'])})\n"
            f"庄家明牌: {_hand_str(g['d'], True)}\n" +
            (f"下注: **{bet}** {currency}\n" if bet else "") +
            f"\n发送 **/要牌** 或 **/停牌**")

    @filter.command("要牌")
    async def cmd_hit(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        currency = self._get_currency_name()
        if uid not in _bj_games:
            yield event.plain_result("还没有进行中的游戏，先 /二十一点 开始吧！")
            return
        g = _bj_games[uid]
        g["p"].append(g["deck"].pop())
        pv = _hand_val(g["p"])
        if pv > 21:
            del _bj_games[uid]
            yield event.plain_result(f"抽到 {_hand_str(g['p'])} (点数: **{pv}**)\n\n## 💥 爆牌了！你输了！" + (f" 失去了 {g['bet']} {currency}" if g["bet"] else ""))
        elif pv == 21:
            del _bj_games[uid]; dv = _hand_val(g["d"])
            yield event.plain_result(f"## 🎉 **21点！**\n\n庄家: {_hand_str(g['d'])} (点数: {dv})\n{_bj_result(pv, dv, g['bet'], uid, currency)}")
        else:
            yield event.plain_result(f"抽到 {g['p'][-1][0]}{g['p'][-1][1]}\n当前: {_hand_str(g['p'])} (点数: **{pv}**)\n/要牌 继续 /停牌 停止")

    @filter.command("停牌")
    async def cmd_stand(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        currency = self._get_currency_name()
        if uid not in _bj_games:
            yield event.plain_result("还没有进行中的游戏！")
            return
        g = _bj_games.pop(uid)
        while _hand_val(g["d"]) < 17: g["d"].append(g["deck"].pop())
        pv, dv = _hand_val(g["p"]), _hand_val(g["d"])
        yield event.plain_result(
            f"你停牌了！\n\n你的手牌: {_hand_str(g['p'])} (点数: **{pv}**)\n"
            f"庄家: {_hand_str(g['d'])} (点数: **{dv}**)\n\n{_bj_result(pv, dv, g['bet'], uid, currency)}")

    async def terminate(self):
        logger.info("[Games] 插件已卸载")


def _bj_result(pv, dv, bet, uid, currency: str = "棱镜币"):
    if dv > 21 or pv > dv:
        w = bet * 2
        if bet: _add_coins(uid, w, "21点获胜")
        return f"## 🎉 你赢了！" + (f" 获得 **{w}** {currency}！" if bet else "")
    elif pv < dv:
        return f"## 😢 庄家赢了！" + (f" 失去了 **{bet}** {currency}" if bet else "")
    else:
        if bet: _add_coins(uid, bet, "21点平局返还")
        return f"## 🤝 平局！" + (f" 下注已退还" if bet else "")
