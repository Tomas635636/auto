# ==========================================================
# Neworld 终极自动签到脚本（工程稳定版）
# ==========================================================

import os
import re
import time
import logging
from datetime import datetime, timedelta, timezone

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==================== 基本配置 ====================

LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

LOG_FILE = "run.log"

# 每次签到赠送流量（GB）
SIGN_BONUS_GB = 0.5

# 如果计算出来的“消耗”超过这个值，认为是异常（防止套餐重置/数据错误）
MAX_REASONABLE_USED_GB = 10.0

# ==================== 标准化文案 ====================

USED_FIRST = "首次记录"
USED_INVALID = "无法统计"
USED_ERROR = "昨日异常"

# ==================== 日志系统 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def log(msg: str):
    logging.info(msg)

# ==================== 时区（北京时间） ====================

CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    return datetime.now(CN_TZ)

def ts_cn_str():
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")

def today_cn_str():
    return now_cn().strftime("%Y-%m-%d")

# ==================== Telegram ====================

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_send(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except:
        pass

# ==================== 邮箱脱敏 ====================

def mask_email(email: str):
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        name_mask = name[0] + "***"
    else:
        name_mask = name[:2] + "***" + name[-2:]
    d0 = domain.split(".")[0]
    suffix = "." + ".".join(domain.split(".")[1:])
    return f"{name_mask}@{d0[:1]}***{suffix}"

# ==================== Chrome 初始化 ====================

def init_chrome():
    from webdriver_manager.chrome import ChromeDriverManager
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def save_screen(driver, name):
    try:
        fn = f"{now_cn().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(fn)
        log(f"📸 Screenshot saved: {fn}")
    except:
        pass

# ==================== SIGNED 日志相关 ====================

def signed_file(slot):
    return f"SIGNED_{slot}.txt"

FINAL_STATUSES = {"SUCCESS", "ALREADY_DONE", "CHECK_NO_CONFIG"}

def has_done_today(slot):
    path = signed_file(slot)
    if not os.path.exists(path):
        return False
    today = today_cn_str()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if today in line and any(s in line for s in FINAL_STATUSES):
                return True
    return False

# ==================== 读取上一条有效记录 ====================

def get_last_record(slot):
    """
    返回：
      (last_remaining: float or None, last_status: str or None)
    """
    path = signed_file(slot)
    if not os.path.exists(path):
        return None, None

    last_remaining = None
    last_status = None

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 6:
                continue

            status = parts[3]
            rem_part = parts[4]

            # 只关心有 remaining 的记录
            m = re.search(r"remaining=([0-9.]+)GB", rem_part)
            if m:
                try:
                    last_remaining = float(m.group(1))
                    last_status = status
                except:
                    pass

    return last_remaining, last_status

# ==================== 写入日志 ====================

def append_signed(slot, status, email, remaining="-", used="-", expire="-", detail="-"):
    line = (
        f"{ts_cn_str()} | {slot} | {email} | {status} | "
        f"remaining={remaining} | used={used} | expire={expire} | detail={detail}\n"
    )
    with open(signed_file(slot), "a", encoding="utf-8") as f:
        f.write(line)

# ==================== 页面解析 ====================

def extract_remaining_and_expire(driver):
    body = driver.find_element(By.TAG_NAME, "body").text
    remaining = "-"
    expire = "-"

    m1 = re.search(r"剩余流量\s*([0-9.]+\s*(GB|MB|TB))", body)
    if m1:
        remaining = m1.group(1).replace(" ", "")

    for line in body.splitlines():
        if "到期" in line:
            m2 = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m2:
                expire = m2.group(1)
                break

    if expire == "-":
        all_times = re.findall(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", body)
        if all_times:
            expire = max(all_times)

    return remaining, expire

# ==================== Telegram 模板 ====================

def tg_success(slot, email, remaining, used, expire):
    tg_send(
f"""🟢 *Neworld 自动签到成功*

👤 *账号:* `{slot}`
📧 *邮箱:* `{email}`

📦 *剩余流量:* `{remaining}`
📉 *昨日消耗:* `{used}`
⏳ *到期时间:* `{expire}`

🕒 *时间:* `{ts_cn_str()}`
🤖 *执行器:* GitHub Actions
""")

def tg_already(slot, email, remaining, expire):
    tg_send(
f"""🟡 *Neworld 今日已签到*

👤 *账号:* `{slot}`
📧 *邮箱:* `{email}`

📦 *剩余流量:* `{remaining}`
⏳ *到期时间:* `{expire}`

🕒 *时间:* `{ts_cn_str()}`
""")

def tg_skip(slot):
    tg_send(
f"""🟠 *Neworld 跳过执行*

👤 *账号:* `{slot}`
⚠️ *原因:* 未配置账号密码

🕒 *时间:* `{ts_cn_str()}`
""")

def tg_failed(slot, email, err):
    tg_send(
f"""🔴 *Neworld 签到失败*

👤 *账号:* `{slot}`
📧 *邮箱:* `{email}`

❌ *错误:* `{err}`

🕒 *时间:* `{ts_cn_str()}`
""")

# ==================== 主流程 ====================

def main():
    slot = os.environ.get("SLOT_NAME", "UNKNOWN")
    username = os.environ.get("USERNAME", "")
    password = os.environ.get("PASSWORD", "")
    email_masked = mask_email(username)

    log(f"🚀 Start signin | Slot={slot} | Account={email_masked}")

    # ---------- 如果今天已经有最终记录 ----------
    if has_done_today(slot):
        log("🟡 Already done today, skip.")
        tg_already(slot, email_masked, "-", "-")
        return

    # ---------- 如果没配置账号 ----------
    if not username or not password:
        append_signed(slot, "CHECK_NO_CONFIG", email_masked)
        tg_skip(slot)
        return

    driver = init_chrome()

    try:
        # ---------- 打开登录页 ----------
        driver.get(LOGIN_URL)
        save_screen(driver, "login_page")

        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.send_keys(username)
        pwd_input.send_keys(password)

        driver.find_element(By.ID, "login-dashboard").click()
        time.sleep(3)

        # ---------- 进入用户中心 ----------
        driver.get(USER_CENTER_URL)
        time.sleep(3)
        save_screen(driver, "user_center")

        # ---------- 抓取当前剩余流量 & 到期时间 ----------
        remaining, expire = extract_remaining_and_expire(driver)

        # ---------- 读取上一条记录 ----------
        last_remaining, last_status = get_last_record(slot)

        # ---------- 计算昨日消耗（应用标准化文案体系） ----------
        used = USED_FIRST

        if last_remaining is None:
            used = USED_FIRST
        elif last_status not in ("SUCCESS", "ALREADY_DONE"):
            used = USED_ERROR
        else:
            try:
                if not remaining.endswith("GB"):
                    used = USED_INVALID
                else:
                    cur = float(remaining.replace("GB", ""))
                    # 核心公式：真实消耗 = (上次剩余 - 本次剩余) + 签到奖励
                    delta = last_remaining - cur + SIGN_BONUS_GB

                    # 合理性校验
                    if 0 <= delta <= MAX_REASONABLE_USED_GB:
                        used = f"{delta:.2f}GB"
                    else:
                        used = USED_INVALID
            except:
                used = USED_INVALID

        # ---------- 查找签到按钮 ----------
        sign_btn = driver.find_element(By.ID, "check-in")
        btn_text = sign_btn.text or ""

        # ---------- 如果已经签过 ----------
        if "已" in btn_text or "成功" in btn_text:
            append_signed(slot, "ALREADY_DONE", email_masked, remaining, used, expire)
            tg_already(slot, email_masked, remaining, expire)
            return

        # ---------- 执行签到 ----------
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        # ---------- 刷新页面，获取签到后的新数据 ----------
        driver.refresh()
        time.sleep(3)
        save_screen(driver, "after_refresh")

        remaining, expire = extract_remaining_and_expire(driver)

        # ---------- 写入 SUCCESS ----------
        append_signed(slot, "SUCCESS", email_masked, remaining, used, expire)
        tg_success(slot, email_masked, remaining, used, expire)

    except Exception as e:
        save_screen(driver, "ERROR")
        append_signed(slot, "FAILED", email_masked, "-", "-", "-", str(e))
        tg_failed(slot, email_masked, str(e))

    finally:
        try:
            driver.quit()
        except:
            pass
        log("🛑 Script end.")

if __name__ == "__main__":
    main()
