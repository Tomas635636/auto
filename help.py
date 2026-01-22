# Neworld 自动签到脚本（终极稳定版 + 多 Slot + 追加日志 + Telegram 通知）

import os
import time
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

# ===== 从环境变量读取 =====
SLOT_NAME = os.environ.get("SLOT_NAME", "UNKNOWN")
USERNAME = os.environ.get("USERNAME", "").strip()
PASSWORD = os.environ.get("PASSWORD", "").strip()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# ===== 文件名 =====
LOG_FILE = "run.log"
MARK_FILE = f"SIGNED_{SLOT_NAME}.txt"

# ===== 日志系统 =====
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

def now_cn():
    return datetime.now(ZoneInfo("Asia/Shanghai"))

def send_tg(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=10)
    except:
        pass

def init_chrome():
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")

    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    driver.implicitly_wait(10)
    return driver

def save_screen(driver, name):
    try:
        filename = f"{now_cn().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(filename)
        log(f"📸 已保存截图: {filename}")
    except:
        pass

def append_mark(text):
    with open(MARK_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")

def already_signed_today():
    if not os.path.exists(MARK_FILE):
        return False

    today = now_cn().strftime("%Y-%m-%d")
    with open(MARK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if today in line and "SUCCESS" in line:
                return True
    return False

def mask_email(email: str):
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        return name[0] + "***@" + domain
    return name[:2] + "***@" + domain

def main():
    log(f"🚀 启动签到任务 Slot={SLOT_NAME}")

    if not USERNAME or not PASSWORD:
        log("❌ 未配置账号密码，退出")
        return

    masked = mask_email(USERNAME)

    # ===== 今日已成功则直接退出 =====
    if already_signed_today():
        msg = f"ℹ️ {masked} ({SLOT_NAME}) 今日已签到，跳过执行"
        log(msg)
        send_tg(msg)
        return

    driver = init_chrome()

    try:
        # ===== 登录 =====
        log("🌐 打开登录页")
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")

        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(USERNAME)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        login_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "login-dashboard")))
        login_btn.click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)

        # ===== 进入用户中心 =====
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)

        # ===== 找签到按钮 =====
        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = sign_btn.text.strip()
        log(f"📌 当前按钮文字：{btn_text}")

        # ===== 已签到 =====
        if "已" in btn_text or "成功" in btn_text:
            t = now_cn().strftime("%Y-%m-%d %H:%M:%S")
            append_mark(f"{t} ALREADY")
            msg = f"ℹ️ {masked} ({SLOT_NAME}) 已经签到过了"
            log(msg)
            send_tg(msg)
            return

        # ===== 点击签到 =====
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(1)
        sign_btn.click()
        time.sleep(3)

        # ===== 再次检测 =====
        try:
            sign_btn2 = driver.find_element(By.ID, "check-in")
            new_text = sign_btn2.text.strip()
        except:
            new_text = "DISAPPEARED"

        t = now_cn().strftime("%Y-%m-%d %H:%M:%S")

        if "已" in new_text or "成功" in new_text or new_text == "DISAPPEARED":
            append_mark(f"{t} SUCCESS")
            msg = f"✅ {masked} ({SLOT_NAME}) 签到成功"
            log(msg)
            send_tg(msg)
        else:
            append_mark(f"{t} UNKNOWN: {new_text}")
            msg = f"⚠️ {masked} ({SLOT_NAME}) 状态未知: {new_text}"
            log(msg)
            send_tg(msg)

    except Exception as e:
        t = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        append_mark(f"{t} ERROR: {e}")
        log(f"❌ 执行异常: {e}")
        save_screen(driver, "ERROR")
        send_tg(f"❌ {masked} ({SLOT_NAME}) 执行异常：{e}")

    finally:
        driver.quit()
        log("🛑 任务结束")

if __name__ == "__main__":
    main()
