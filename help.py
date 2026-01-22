# Neworld 自动签到脚本（多账号 slot + 跨运行记忆 + Telegram 通知 + 流量/到期抓取）
# 功能说明：
# 1. 每个 slot 对应一个 SIGNED_SLOT?.txt，永远追加写，不删除旧记录
# 2. 同一天如果已经 SUCCESS 或 ALREADY，则后续触发不会再登录（降低封号风险）
# 3. 如果 slot 未配置（缺账号/密码），也会发 TG 提醒：未配置
# 4. 每次运行都会抓取：
#    - 剩余流量
#    - 到期时间（yyyy-mm-dd HH:MM:SS）
# 5. 所有运行过程写入 run.log，截图保存为 png（但不会提交到仓库）

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

# ================== 网站地址 ==================
LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

# ================== 日志文件 ==================
LOG_FILE = "run.log"

# ================== 日志系统 ==================
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

# ================== 时区（北京时间） ==================
CN_TZ = timezone(timedelta(hours=8))

def now_cn() -> datetime:
    return datetime.now(CN_TZ)

def today_cn_str() -> str:
    return now_cn().strftime("%Y-%m-%d")

def ts_cn_str() -> str:
    # 年月日 时分秒
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")

# ================== Telegram 通知 ==================
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_notify(text: str):
    """发送 TG 消息（如果 token/chat_id 未配置则静默跳过）"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
    except:
        pass

# ================== 邮箱脱敏 ==================
def mask_email(email: str) -> str:
    """
    举例：
      abcdefg@gmail.com -> ab***fg@g***.com
      a@xx.com -> a***@x***.com
    """
    email = (email or "").strip()
    if "@" not in email:
        return "***"

    name, domain = email.split("@", 1)

    # 用户名脱敏
    if len(name) <= 2:
        name_mask = name[0] + "***"
    else:
        name_mask = name[:2] + "***" + name[-2:]

    # 域名脱敏（只显示首字母 + 后缀）
    if "." in domain:
        d0 = domain.split(".")[0]
        suffix = "." + ".".join(domain.split(".")[1:])
    else:
        d0, suffix = domain, ""

    d0_mask = (d0[:1] if d0 else "x") + "***"
    return f"{name_mask}@{d0_mask}{suffix}"

# ================== Chrome 初始化 ==================
def init_chrome():
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")

    # 模拟真实浏览器 UA
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
    """保存当前页面截图（调试用）"""
    try:
        filename = f"{now_cn().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(filename)
        log(f"📸 已保存截图: {filename}")
    except:
        pass

# ================== SIGNED 文件机制 ==================
def signed_file_path(slot_name: str) -> str:
    return f"SIGNED_{slot_name}.txt"

def parse_signed_success_today(slot_name: str) -> bool:
    """
    判断今天是否已经成功签过：
    - 只要 SIGNED_{slot}.txt 中存在今天日期，且包含 SUCCESS 或 ALREADY
    - 就认为今天已完成，不再登录
    """
    path = signed_file_path(slot_name)
    if not os.path.exists(path):
        return False

    today = today_cn_str()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if today in line and ("SUCCESS" in line or "ALREADY" in line):
                    return True
    except:
        return False
    return False

def append_signed_log(slot_name: str, status: str, email_masked: str,
                      remaining: str = "-", expire_at: str = "-", detail: str = "-"):
    """
    永远追加写一行，方便回溯历史

    示例：
    2026-01-22 11:16:03 | SLOT2 | ab***fg@g***.com | SUCCESS | remaining=20GB | expire=2026-01-24 13:57:41 | detail=clicked
    """
    line = (
        f"{ts_cn_str()} | {slot_name} | {email_masked} | {status} | "
        f"remaining={remaining} | expire={expire_at} | detail={detail}\n"
    )
    path = signed_file_path(slot_name)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

# ================== 抓取“剩余流量 / 到期时间” ==================
def extract_remaining_and_expire(driver):
    """
    从用户中心页面提取：
      - 剩余流量（例如：19.87GB）
      - 到期时间（例如：2026-01-24 13:57:41）

    设计原则：
      - 不依赖 class / id
      - 不依赖“到期”这两个字
      - 只要页面中出现 yyyy-mm-dd HH:MM:SS 这种格式就抓出来
      - 网页改版也能抗住
    """
    remaining = "-"
    expire_at = "-"

    # 取整页可见文本
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except:
        body_text = ""

    # ===== 剩余流量 =====
    # 匹配：剩余流量 19.87GB
    m1 = re.search(r"剩余流量\s*([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB|TB))", body_text, re.IGNORECASE)
    if m1:
        remaining = m1.group(1).replace(" ", "")

    # ===== 到期时间 =====
    # 只要页面中出现：2026-01-24 13:57:41 这种格式就抓
    m2 = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", body_text)
    if m2:
        expire_at = m2.group(1)

    return remaining, expire_at

# ================== 主流程 ==================
def main():
    slot_name = os.environ.get("SLOT_NAME", "").strip() or "UNKNOWN_SLOT"
    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    email_masked = mask_email(username)

    log("🚀 启动自动签到脚本")
    log(f"🧩 当前 slot: {slot_name} | 账号: {email_masked}")

    # 1) 如果今天已经成功过，直接退出（防止频繁登录）
    if parse_signed_success_today(slot_name):
        msg = f"✅ {slot_name} 今日已完成签到（跳过登录）\n账号：{email_masked}"
        log(msg)
        tg_notify(msg)
        return

    # 2) 如果未配置账号密码
    if not username or not password:
        append_signed_log(slot_name, "NOT_CONFIGURED", email_masked, "-", "-", "missing secrets")
        msg = (
            f"⚠️ {slot_name} 未配置账号/密码（跳过）\n"
            f"账号：{email_masked}\n"
            f"状态：未配置"
        )
        log(msg)
        tg_notify(msg)
        return

    driver = init_chrome()

    try:
        # 3) 打开登录页
        log("🌐 打开登录页")
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        save_screen(driver, "login_page")

        # 4) 输入账号密码
        log("✍️ 输入账号密码")
        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(username)
        pwd_input.clear()
        pwd_input.send_keys(password)
        save_screen(driver, "filled_form")

        # 5) 点击登录
        log("🔐 点击登录按钮")
        login_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "login-dashboard")))
        login_btn.click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        save_screen(driver, "after_login")
        log("✅ 登录成功")

        # 6) 进入用户中心
        log("🏠 进入用户中心")
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        save_screen(driver, "user_center")

        # 7) 抓取流量 / 到期时间
        remaining, expire_at = extract_remaining_and_expire(driver)
        log(f"📦 剩余流量：{remaining} | ⏳ 到期时间：{expire_at}")

        # 8) 查找签到按钮
        log("🔍 查找签到按钮")
        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = (sign_btn.text or "").strip()
        log(f"📌 签到按钮文字：{btn_text}")

        # 9) 如果网页显示已签到
        if ("已" in btn_text) or ("成功" in btn_text):
            append_signed_log(slot_name, "ALREADY", email_masked, remaining, expire_at, f"btn={btn_text}")
            msg = (
                f"✅ {slot_name} 已签到（网页检测）\n"
                f"账号：{email_masked}\n"
                f"剩余流量：{remaining}\n"
                f"到期时间：{expire_at}"
            )
            tg_notify(msg)
            return

        # 10) 点击签到
        log("🖱️ 点击签到按钮")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(1)
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        # 11) 再次检测状态
        status = "UNKNOWN"
        detail = "clicked"
        try:
            sign_btn2 = driver.find_element(By.ID, "check-in")
            new_text = (sign_btn2.text or "").strip()
            log(f"📌 点击后按钮文字：{new_text}")
            if ("已" in new_text) or ("成功" in new_text):
                status = "SUCCESS"
                detail = f"btn_after={new_text}"
            else:
                status = "UNKNOWN"
                detail = f"btn_after={new_text}"
        except:
            # 按钮消失，也认为成功
            status = "SUCCESS"
            detail = "btn_disappeared"

        append_signed_log(slot_name, status, email_masked, remaining, expire_at, detail)

        if status == "SUCCESS":
            msg = (
                f"✅ {slot_name} 签到成功\n"
                f"账号：{email_masked}\n"
                f"剩余流量：{remaining}\n"
                f"到期时间：{expire_at}"
            )
        else:
            msg = (
                f"⚠️ {slot_name} 签到状态未知\n"
                f"账号：{email_masked}\n"
                f"剩余流量：{remaining}\n"
                f"到期时间：{expire_at}\n"
                f"说明：{detail}"
            )

        tg_notify(msg)

    except Exception as e:
        save_screen(driver, "ERROR")

        remaining, expire_at = "-", "-"
        try:
            remaining, expire_at = extract_remaining_and_expire(driver)
        except:
            pass

        append_signed_log(slot_name, "ERROR", email_masked, remaining, expire_at, f"{type(e).__name__}: {e}")

        msg = (
            f"❌ {slot_name} 签到失败\n"
            f"账号：{email_masked}\n"
            f"剩余流量：{remaining}\n"
            f"到期时间：{expire_at}\n"
            f"错误：{type(e).__name__}: {e}"
        )
        tg_notify(msg)

    finally:
        try:
            driver.quit()
        except:
            pass
        log("🛑 脚本结束")

if __name__ == "__main__":
    main()
