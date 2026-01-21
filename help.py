# Neworld 自动签到脚本（终极稳定 + 多Slot + 跨运行记忆版）
import os
import time
import logging
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

# ========= 日志系统 =========
LOG_FILE = "run.log"
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

# ========= Slot 标记机制 =========
def get_slot_and_markfile():
    slot = os.environ.get("SLOT", "").strip()
    if not slot:
        log("❌ 未获取到 SLOT 环境变量")
        return None, None

    today = datetime.now().strftime("%Y-%m-%d")
    mark_file = f".signin_done_slot{slot}.txt"
    return slot, mark_file, today

# ========= 浏览器 =========
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
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(filename)
        log(f"📸 已保存截图: {filename}")
    except:
        pass

def main():
    log("🚀 启动自动签到脚本")

    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    if not username or not password:
        log("❌ 未获取到账号或密码")
        return

    slot = os.environ.get("SLOT", "").strip()
    if not slot:
        log("❌ 未指定 SLOT")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    mark_file = f".signin_done_slot{slot}.txt"

    log(f"👤 当前账号（Slot{slot}）：{username}")

    # ========= 0. 先检查是否已经签过 =========
    if os.path.exists(mark_file):
        with open(mark_file, "r", encoding="utf-8") as f:
            old = f.read().strip()
        if old == today:
            log(f"✅ Slot{slot} 今天已经签过到（标记文件存在），直接退出")
            return

    driver = None

    try:
        driver = init_chrome()

        # ========== 1. 打开登录页 ==========
        log("🌐 打开登录页")
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        save_screen(driver, "login_page")

        # ========== 2. 输入账号密码 ==========
        log("✍️ 输入账号密码")
        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(username)
        pwd_input.clear()
        pwd_input.send_keys(password)
        save_screen(driver, "filled_form")

        # ========== 3. 点击登录 ==========
        log("🔐 点击登录按钮")
        login_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "login-dashboard")))
        login_btn.click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        save_screen(driver, "after_login")
        log("✅ 登录成功")

        # ========== 4. 进入用户中心 ==========
        log("🏠 进入用户中心")
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        save_screen(driver, "user_center")

        # ========== 5. 查找签到按钮 ==========
        log("🔍 查找签到按钮")
        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = sign_btn.text.strip()
        log(f"📌 按钮文字：{btn_text}")

        # ========== 6. 如果已签到 ==========
        if "已" in btn_text or "成功" in btn_text:
            log(f"🎉 Slot{slot} 今天已经是签到状态（可能是手动签的）")
            with open(mark_file, "w", encoding="utf-8") as f:
                f.write(today)
            log(f"📝 已写入签到标记文件: {mark_file}")
            return

        # ========== 7. 点击签到 ==========
        log("🖱️ 点击签到按钮")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(1)
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        # ========== 8. 再次检测状态 ==========
        try:
            sign_btn2 = driver.find_element(By.ID, "check-in")
            new_text = sign_btn2.text.strip()
            log(f"📌 点击后按钮文字：{new_text}")
            if "已" in new_text or "成功" in new_text:
                log(f"🎉 Slot{slot} 签到成功！")
                with open(mark_file, "w", encoding="utf-8") as f:
                    f.write(today)
                log(f"📝 已写入签到标记文件: {mark_file}")
            else:
                log("⚠️ 状态未知，可能页面改版")
        except:
            log(f"🎉 Slot{slot} 签到成功（按钮已消失）")
            with open(mark_file, "w", encoding="utf-8") as f:
                f.write(today)
            log(f"📝 已写入签到标记文件: {mark_file}")

    except Exception as e:
        log(f"❌ 执行出错：{e}")
        if driver:
            save_screen(driver, "ERROR")

    finally:
        if driver:
            driver.quit()
        log("🛑 脚本结束")

if __name__ == "__main__":
    main()
