# Neworld 自动签到脚本（多账号 slot + 跨运行记忆 + Telegram 通知 + 流量/到期抓取 + 强制刷新）
# Ultimate Stable Edition

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

# ================= 基本配置 =================
LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"
LOG_FILE = "run.log"

# ================= 日志 =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def log(msg):
    logging.info(msg)

# ================= 时区 =================
CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    return datetime.now(CN_TZ)

def today_cn_str():
    return now_cn().strftime("%Y-%m-%d")

def ts_cn_str():
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")

# ================= Telegram =================
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
    except:
        pass

# ================= 工具函数 =================
def mask_email(email):
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    return name[:2] + "***@" + domain[:2] + "***"

def init_chrome():
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.implicitly_wait(10)
    return driver

def save_screen(driver, name):
    try:
        fn = f"{now_cn().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(fn)
        log(f"📸 Screenshot saved: {fn}")
    except:
        pass

# ================= SIGNED 文件 =================
def signed_file_path(slot):
    return f"SIGNED_{slot}.txt"

FINAL_STATUSES = {"SUCCESS", "ALREADY_DONE", "CHECK_NO_CONFIG"}

def has_final_status_today(slot):
    path = signed_file_path(slot)
    if not os.path.exists(path):
        return False
    today = today_cn_str()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(today):
                if any(s in line for s in FINAL_STATUSES):
                    return True
    return False

def append_signed_log(slot, status, email, remaining="-", expire="-", detail="-"):
    line = f"{ts_cn_str()} | {slot} | {email} | {status} | remaining={remaining} | expire={expire} | detail={detail}\n"
    with open(signed_file_path(slot), "a", encoding="utf-8") as f:
        f.write(line)

# ================= 页面解析 =================
def extract_remaining_and_expire(driver):
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except:
        body = ""

    remaining = "-"
    expire = "-"

    m1 = re.search(r"剩余流量\s*([0-9.]+\s*(?:GB|MB|TB))", body)
    if m1:
        remaining = m1.group(1).replace(" ", "")

    times = re.findall(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", body)
    if times:
        expire = max(times)

    return remaining, expire

# ================= 主流程 =================
def main():
    slot = os.environ.get("SLOT_NAME", "UNKNOWN_SLOT")
    username = os.environ.get("USERNAME", "")
    password = os.environ.get("PASSWORD", "")
    email_masked = mask_email(username)

    log(f"🚀 Start signin | Slot={slot} | Account={email_masked}")

    # ===== 未配置 =====
    if not username or not password:
        append_signed_log(slot, "CHECK_NO_CONFIG", email_masked, "-", "-", "missing secrets")
        tg_notify(
            f"⚠️【Neworld 未配置账号】\n\n"
            f"🧩 槽位：{slot}\n"
            f"👤 账号：***\n"
            f"💥 说明：missing secrets\n\n"
            f"🕒 时间：{ts_cn_str()}"
        )
        return

    # ===== 防重复 =====
    if has_final_status_today(slot):
        log("Already done today, skip.")
        return

    driver = init_chrome()

    try:
        # ===== 登录 =====
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        save_screen(driver, "login_page")

        driver.find_element(By.ID, "email").send_keys(username)
        driver.find_element(By.ID, "passwd").send_keys(password)
        driver.find_element(By.ID, "login-dashboard").click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        save_screen(driver, "after_login")

        # ===== 进入用户中心 =====
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        save_screen(driver, "user_center")

        remaining, expire = extract_remaining_and_expire(driver)

        sign_btn = driver.find_element(By.ID, "check-in")
        btn_text = sign_btn.text.strip()

        # ===== 已签到 =====
        if "已" in btn_text or "成功" in btn_text:
            append_signed_log(slot, "ALREADY_DONE", email_masked, remaining, expire, f"btn={btn_text}")
            tg_notify(
                f"☑️【Neworld 今日已签到】\n\n"
                f"👤 账号：{email_masked}\n"
                f"🧩 槽位：{slot}\n"
                f"📦 剩余流量：{remaining}\n"
                f"⏳ 到期时间：{expire}\n\n"
                f"🕒 时间：{ts_cn_str()}"
            )
            return

        # ===== 点击签到 =====
        sign_btn.click()
        time.sleep(5)
        save_screen(driver, "after_click")

        # ===== 强制刷新 =====
        driver.refresh()
        time.sleep(5)
        save_screen(driver, "after_refresh")

        remaining, expire = extract_remaining_and_expire(driver)

        append_signed_log(slot, "SUCCESS", email_masked, remaining, expire, "clicked")

        tg_notify(
            f"🎉【Neworld 自动签到成功】\n\n"
            f"👤 账号：{email_masked}\n"
            f"🧩 槽位：{slot}\n"
            f"📦 剩余流量：{remaining}\n"
            f"⏳ 到期时间：{expire}\n\n"
            f"🕒 时间：{ts_cn_str()}"
        )

    except Exception as e:
        save_screen(driver, "ERROR")
        append_signed_log(slot, "FAILED", email_masked, "-", "-", str(e))

        tg_notify(
            f"❌【Neworld 签到失败】\n\n"
            f"👤 账号：{email_masked}\n"
            f"🧩 槽位：{slot}\n"
            f"💥 错误：{type(e).__name__}: {e}\n\n"
            f"🕒 时间：{ts_cn_str()}"
        )

    finally:
        try:
            driver.quit()
        except:
            pass
        log("🛑 Script finished")

if __name__ == "__main__":
    main()
