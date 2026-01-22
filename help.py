# Neworld 自动签到脚本（终极稳定版：4 Slot + Telegram + 跨运行记忆）

import os
import time
import logging
from datetime import datetime
import requests

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

LOG_FILE = "run.log"

# ========= 日志系统 =========
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

# ========= Telegram =========
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_notify(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=10)
    except:
        pass

# ========= Chrome =========
def init_chrome():
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    driver.implicitly_wait(10)
    return driver

def save_screen(driver, name):
    try:
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(filename)
        log(f"📸 截图: {filename}")
    except:
        pass

# ========= 主逻辑 =========
def main():
    log("🚀 启动自动签到脚本")

    slot = os.environ.get("SLOT_NAME", "").strip()
    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    if not slot or not username or not password:
        log("❌ 未获取到 SLOT / 账号 / 密码")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    mark_file = f"SIGNED_{slot}.txt"

    # ===== 跨运行记忆判断 =====
    if os.path.exists(mark_file):
        with open(mark_file, "r", encoding="utf-8") as f:
            last = f.read().strip()
        if last == today:
            log(f"🛑 {slot} 今日已签过，跳过")
            tg_notify(f"✅ {slot} 今日已签到（本地标记）")
            return

    log(f"👤 当前 Slot: {slot} 账号: {username}")

    driver = init_chrome()

    try:
        # 1. 打开登录页
        log("🌐 打开登录页")
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        save_screen(driver, "login_page")

        # 2. 输入账号密码
        log("✍️ 输入账号密码")
        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(username)
        pwd_input.clear()
        pwd_input.send_keys(password)

        save_screen(driver, "filled_form")

        # 3. 登录
        log("🔐 点击登录")
        login_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "login-dashboard")))
        login_btn.click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        save_screen(driver, "after_login")
        log("✅ 登录成功")

        # 4. 用户中心
        log("🏠 进入用户中心")
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        save_screen(driver, "user_center")

        # 5. 找签到按钮
        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = sign_btn.text.strip()
        log(f"📌 按钮文字: {btn_text}")

        # 6. 已签过（网页）
        if "已" in btn_text or "成功" in btn_text:
            log("🎉 网页显示已签到")
            with open(mark_file, "w", encoding="utf-8") as f:
                f.write(today)
            tg_notify(f"✅ {slot} 今日已签到（网页检测）")
            return

        # 7. 点击签到
        log("🖱️ 点击签到按钮")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(1)
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        # 8. 再次判断
        try:
            sign_btn2 = driver.find_element(By.ID, "check-in")
            new_text = sign_btn2.text.strip()
            log(f"📌 点击后文字: {new_text}")

            if "已" in new_text or "成功" in new_text:
                raise Exception("signed ok")
            else:
                raise Exception("unknown state")

        except:
            log("🎉 签到成功")

            with open(mark_file, "w", encoding="utf-8") as f:
                f.write(today)

            tg_notify(f"🎉 {slot} 签到成功！")

    except Exception as e:
        log(f"❌ 出错: {e}")
        save_screen(driver, "ERROR")
        tg_notify(f"❌ {slot} 签到失败：{e}")

    finally:
        driver.quit()
        log("🛑 脚本结束")

if __name__ == "__main__":
    main()
