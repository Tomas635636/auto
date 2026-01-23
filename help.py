# Neworld 自动签到脚本（多账号 slot + 跨运行记忆 + Telegram 通知 + 流量/到期抓取 + 失败自动重试）

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

LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

LOG_FILE = "run.log"

# ========== 重试参数 ==========
MAX_RETRY = 2          # 失败后最多再重试 2 次（总共最多跑 3 次）
RETRY_SLEEP = 30       # 每次失败后等待 30 秒再试

# ========== 日志 ==========
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

# ========== 时区（北京时间）==========
CN_TZ = timezone(timedelta(hours=8))

def now_cn() -> datetime:
    return datetime.now(CN_TZ)

def today_cn_str() -> str:
    return now_cn().strftime("%Y-%m-%d")

def ts_cn_str() -> str:
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")

# ========== Telegram 通知 ==========
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_notify(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
    except:
        pass

# ========== 邮箱脱敏 ==========
def mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        name_mask = name[0] + "***"
    else:
        name_mask = name[:2] + "***" + name[-2:]
    if "." in domain:
        d0 = domain.split(".")[0]
        suffix = "." + ".".join(domain.split(".")[1:])
    else:
        d0, suffix = domain, ""
    d0_mask = (d0[:1] if d0 else "x") + "***"
    return f"{name_mask}@{d0_mask}{suffix}"

# ========== Chrome 初始化 ==========
def init_chrome():
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")

    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    driver.implicitly_wait(10)
    return driver

def save_screen(driver, name: str):
    try:
        filename = f"{now_cn().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(filename)
        log(f"📸 已保存截图: {filename}")
    except:
        pass

# ========== SIGNED 文件 ==========
def signed_file_path(slot_name: str) -> str:
    return f"SIGNED_{slot_name}.txt"

FINAL_STATUSES = {"SUCCESS", "ALREADY_DONE", "CHECK_NO_CONFIG"}

def has_final_status_today(slot_name: str) -> bool:
    path = signed_file_path(slot_name)
    if not os.path.exists(path):
        return False

    today = today_cn_str()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith(today):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    status = parts[3]
                    if status in FINAL_STATUSES:
                        return True
    except:
        return False
    return False

def append_signed_log(slot_name: str, status: str, email_masked: str,
                      remaining: str = "-", expire_at: str = "-", detail: str = "-"):
    line = (
        f"{ts_cn_str()} | {slot_name} | {email_masked} | {status} | "
        f"remaining={remaining} | expire={expire_at} | detail={detail}\n"
    )
    path = signed_file_path(slot_name)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

# ========== 从用户中心页面提取“剩余流量 / 到期时间”==========
def extract_remaining_and_expire(driver):
    remaining = "-"
    expire_at = "-"

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except:
        body_text = ""

    m1 = re.search(r"剩余流量\s*([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB|TB))", body_text, re.IGNORECASE)
    if m1:
        remaining = m1.group(1).replace(" ", "")

    for line in body_text.splitlines():
        if "到期" in line:
            m2 = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
            if m2:
                expire_at = m2.group(1)
                break

    if expire_at == "-":
        all_times = re.findall(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", body_text)
        if all_times:
            expire_at = max(all_times)

    return remaining, expire_at

# ========== 单次执行签到 ==========
def run_once(slot_name, username, password, email_masked):
    driver = init_chrome()
    try:
        log("🌐 打开登录页")
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        save_screen(driver, "login_page")

        log("✍️ 输入账号密码")
        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(username)
        pwd_input.clear()
        pwd_input.send_keys(password)
        save_screen(driver, "filled_form")

        log("🔐 点击登录按钮")
        login_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "login-dashboard")))
        login_btn.click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        save_screen(driver, "after_login")
        log("✅ 登录成功")

        log("🏠 进入用户中心")
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        save_screen(driver, "user_center")

        remaining, expire_at = extract_remaining_and_expire(driver)
        log(f"📦 剩余流量：{remaining} | ⏳ 到期时间：{expire_at}")

        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = (sign_btn.text or "").strip()
        log(f"📌 签到按钮文字：{btn_text}")

        if ("已" in btn_text) or ("成功" in btn_text):
            append_signed_log(slot_name, "ALREADY_DONE", email_masked, remaining, expire_at, f"btn={btn_text}")
            msg = f"✅ {slot_name} 已签到\n账号：{email_masked}\n剩余流量：{remaining}\n到期时间：{expire_at}"
            tg_notify(msg)
            return True

        log("🖱️ 点击签到按钮")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(1)
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        append_signed_log(slot_name, "SUCCESS", email_masked, remaining, expire_at, "clicked")
        msg = f"✅ {slot_name} 签到成功\n账号：{email_masked}\n剩余流量：{remaining}\n到期时间：{expire_at}"
        tg_notify(msg)
        log("🎉 签到完成")
        return True

    finally:
        try:
            driver.quit()
        except:
            pass

# ========== 主流程 ==========
def main():
    slot_name = os.environ.get("SLOT_NAME", "").strip() or "UNKNOWN_SLOT"
    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    email_masked = mask_email(username)

    log("🚀 启动自动签到脚本")
    log(f"🧩 当前 slot: {slot_name} | 账号: {email_masked}")

    if has_final_status_today(slot_name):
        msg = f"✅ {slot_name} 今日已完成（SUCCESS / ALREADY_DONE / CHECK_NO_CONFIG），跳过\n账号：{email_masked}"
        log(msg)
        tg_notify(msg)
        return

    if not username or not password:
        append_signed_log(slot_name, "CHECK_NO_CONFIG", email_masked, "-", "-", "missing secrets")
        msg = f"⚠️ {slot_name} 未配置账号密码\n账号：{email_masked}"
        log(msg)
        tg_notify(msg)
        return

    last_error = None

    for attempt in range(1, MAX_RETRY + 2):
        try:
            log(f"🔁 尝试第 {attempt} 次执行...")
            ok = run_once(slot_name, username, password, email_masked)
            if ok:
                return
        except Exception as e:
            last_error = e
            log(f"❌ 第 {attempt} 次执行失败：{type(e).__name__}: {e}")

        if attempt <= MAX_RETRY:
            log(f"⏳ 等待 {RETRY_SLEEP} 秒后重试...")
            time.sleep(RETRY_SLEEP)

    append_signed_log(slot_name, "FAILED", email_masked, "-", "-", f"{type(last_error).__name__}: {last_error}")
    msg = f"❌ {slot_name} 多次重试后仍然失败\n账号：{email_masked}\n错误：{type(last_error).__name__}: {last_error}"
    log(msg)
    tg_notify(msg)

    log("🛑 脚本结束")

if __name__ == "__main__":
    main()
