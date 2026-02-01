# ========== Neworld 终极自动签到脚本（V2Board API 直连版） ==========

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

# ================== 基本配置 ==================
LOGIN_URL = "https://neworld.cloud/auth/login"
USER_CENTER_URL = "https://neworld.cloud/user"
LOG_FILE = "run.log"

# ================== 日志 ==================
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

# ================== 时区 ==================
CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    return datetime.now(CN_TZ)

def ts_cn_str():
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")

def today_cn_str():
    return now_cn().strftime("%Y-%m-%d")

# ================== Telegram ==================
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

# ================== 邮箱脱敏 ==================
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

# ================== Chrome ==================
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

# ================== SIGNED 文件 ==================
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
            if line.startswith(today) and any(s in line for s in FINAL_STATUSES):
                return True
    return False

def parse_remaining_gb(text: str):
    if not text:
        return None
    m = re.search(r"([0-9.]+)\s*GB", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except:
        return None

def append_signed(slot, status, email, remaining="-", used=None, expire="-", detail="-", history="-"):
    used_part = "-"
    if used is None:
        used_part = "-"
    else:
        used_part = f"{used:.2f}GB"

    line = (
        f"{ts_cn_str()} | {slot} | {email} | {status} | "
        f"remaining={remaining} | used={used_part} | expire={expire} | history={history} | detail={detail}\n"
    )

    with open(signed_file(slot), "a", encoding="utf-8") as f:
        f.write(line)

# ================== 页面解析 ==================
def extract_remaining_and_expire(driver):
    body = driver.find_element(By.TAG_NAME, "body").text
    remaining = "-"
    expire = "-"

    m1 = re.search(r"剩余流量\s*([0-9.]+\s*(GB|MB|TB))", body)
    if m1:
        remaining = m1.group(1)

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

# 从网页源码中提取流量历史数据
def extract_traffic_history(driver):
    try:
        html = driver.page_source
        pattern = r'\\"series\\":\[([\d\.,]+)\]|"series":\[([\d\.,]+)\]'
        match = re.search(pattern, html)
        
        if match:
            history_data = match.group(1) or match.group(2)
            log(f"📊 Traffic history found: {history_data}")
            return history_data
    except Exception as e:
        log(f"⚠️ Failed to extract history: {e}")
    
    return "-"

# [新增] 从 history 字符串中提取最后一个数值作为“昨日消耗”
def extract_last_used_from_history(history_str):
    if not history_str or history_str == "-":
        return None
    try:
        # 分割字符串 "0,0,0.01,0.5" -> 取最后一个 "0.5"
        parts = history_str.split(",")
        if parts:
            return float(parts[-1])
    except:
        pass
    return None

# ================== Telegram 模板 ==================
def tg_success(slot, email, remaining, used, expire):
    used_text = "未获取" if used is None else f"{used:.2f}GB"
    tg_send(
f"""🟢 *Neworld 自动签到成功*

👤 *账号:* `{slot}`
📧 *邮箱:* `{email}`

📊 *状态:* ✅ 签到成功
📦 *剩余流量:* `{remaining}`
📉 *最新消耗:* `{used_text}`
⏳ *到期时间:* `{expire}`

🕒 *时间:* `{ts_cn_str()}`
""")

def tg_already(slot, email, remaining, expire, used):
    used_text = "未获取" if used is None else f"{used:.2f}GB"
    tg_send(
f"""🟡 *Neworld 今日已签到*

👤 *账号:* `{slot}`
📧 *邮箱:* `{email}`

📦 *剩余流量:* `{remaining}`
📉 *最新消耗:* `{used_text}`
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

# ================== 主流程 ==================
def main():
    slot = os.environ.get("SLOT_NAME", "UNKNOWN")
    username = os.environ.get("USERNAME", "")
    password = os.environ.get("PASSWORD", "")
    email_masked = mask_email(username)

    log(f"🚀 Start signin | Slot={slot} | Account={email_masked}")

    if has_done_today(slot):
        log("🟡 Already done today, skip.")
        tg_already(slot, email_masked, "-", "-", None)
        return

    if not username or not password:
        append_signed(slot, "CHECK_NO_CONFIG", email_masked)
        tg_skip(slot)
        return

    driver = init_chrome()

    try:
        driver.get(LOGIN_URL)
        save_screen(driver, "login_page")

        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.send_keys(username)
        pwd_input.send_keys(password)

        driver.find_element(By.ID, "login-dashboard").click()
        time.sleep(3)

        driver.get(USER_CENTER_URL)
        time.sleep(3)
        save_screen(driver, "user_center")

        sign_btn = driver.find_element(By.ID, "check-in")
        btn_text = sign_btn.text

        # === 场景 1: 已经签到过 ===
        if "已" in btn_text or "成功" in btn_text:
            remaining_text, expire = extract_remaining_and_expire(driver)
            history_str = extract_traffic_history(driver)
            # 直接从 history 拿 used
            used_gb = extract_last_used_from_history(history_str)
            
            append_signed(slot, "ALREADY_DONE", email_masked, remaining_text, used_gb, expire, history=history_str)
            tg_already(slot, email_masked, remaining_text, expire, used_gb)
            return

        # === 场景 2: 需要签到 ===
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        driver.refresh()
        time.sleep(3)
        save_screen(driver, "after_refresh")

        remaining_text, expire = extract_remaining_and_expire(driver)
        # 获取 history
        history_str = extract_traffic_history(driver)
        # 直接从 history 拿 used
        used_gb = extract_last_used_from_history(history_str)

        append_signed(slot, "SUCCESS", email_masked, remaining_text, used_gb, expire, history=history_str)
        tg_success(slot, email_masked, remaining_text, used_gb, expire)

    except Exception as e:
        save_screen(driver, "ERROR")
        append_signed(slot, "FAILED", email_masked, "-", None, "-", detail=str(e))
        tg_failed(slot, email_masked, str(e))

    finally:
        try:
            driver.quit()
        except:
            pass
        log("🛑 Script end.")

if __name__ == "__main__":
    main()
