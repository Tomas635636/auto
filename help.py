# Neworld 自动签到脚本（多账号 slot + 跨运行记忆 + Telegram 通知 + 流量/到期抓取）
# - 每个 slot 对应一个 SIGNED_SLOT?.txt，永远追加写，不删除旧记录
# - 同一天如果已经 SUCCESS 或 ALREADY，则后续触发不会再登录（降低封号风险）
# - 如果 slot 未配置（缺账号/密码），也会发 TG 提醒：未配置

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
    # 年月日 时分秒
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")

# ========== Telegram 通知 ==========
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

# ========== 邮箱脱敏 ==========
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
    # name 脱敏
    if len(name) <= 2:
        name_mask = name[0] + "***"
    else:
        name_mask = name[:2] + "***" + name[-2:]

    # domain 脱敏（只展示首字母 + 后缀）
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

    # UA 可以不写，也可以写（写了更像真实浏览器，但也不保证）
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

# ========== SIGNED 文件读写（追加写，不删除旧内容）==========
def signed_file_path(slot_name: str) -> str:
    return f"SIGNED_{slot_name}.txt"

def parse_signed_success_today(slot_name: str) -> bool:
    """
    判断今天是否已经成功签过：
    - 只要 SIGNED_{slot}.txt 中存在今天日期，且包含 SUCCESS 或 ALREADY，就认为今天已完成
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
    永远追加写一行，方便你回溯
    格式示例：
      2026-01-22 11:16:03 | SLOT2 | ab***fg@g***.com | SUCCESS | remaining=20GB | expire=2026-01-24 13:57:41 | detail=clicked
    """
    line = (
        f"{ts_cn_str()} | {slot_name} | {email_masked} | {status} | "
        f"remaining={remaining} | expire={expire_at} | detail={detail}\n"
    )
    path = signed_file_path(slot_name)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

# ========== 从用户中心页面提取“剩余流量 / 到期时间”==========
def extract_remaining_and_expire(driver):
    """
    你的截图里类似：
      <span>剩余流量 20GB</span>
      <p class="my-3">你的账户大约还有 2 天到期（2026-01-24 13:57:41）</p>

    这里做“文本包含”匹配，尽量不依赖 class/id，网页改动更抗打。
    """
    remaining = "-"
    expire_at = "-"

    # 取整页可见文本
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except:
        body_text = ""

    # 剩余流量：抓 “剩余流量” 后面的一段
    # 例：剩余流量 20GB
    m1 = re.search(r"剩余流量\s*([0-9]+(?:\.[0-9]+)?\s*(?:GB|MB|TB))", body_text, re.IGNORECASE)
    if m1:
        remaining = m1.group(1).replace(" ", "")

    # 到期时间：优先抓括号里的 yyyy-mm-dd HH:MM:SS
    m2 = re.search(r"到期[（(]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})\s*[)）]", body_text)
    if m2:
        expire_at = m2.group(1)

    return remaining, expire_at

# ========== 主流程 ==========
def main():
    slot_name = os.environ.get("SLOT_NAME", "").strip() or "UNKNOWN_SLOT"
    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    email_masked = mask_email(username)

    log("🚀 启动自动签到脚本")
    log(f"🧩 当前 slot: {slot_name} | 账号: {email_masked}")

    # 1) 若今天已完成，直接退出（避免频繁登录）
    if parse_signed_success_today(slot_name):
        msg = f"✅ {slot_name} 今日已完成签到（跳过登录）\n账号：{email_masked}"
        log(msg)
        tg_notify(msg)
        return

    # 2) 未配置账号密码：写日志 + TG 提醒
    if not username or not password:
        remaining, expire_at = "-", "-"
        append_signed_log(slot_name, "NOT_CONFIGURED", email_masked, remaining, expire_at, "missing secrets")
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

        # 7) 先抓“剩余流量/到期时间”
        remaining, expire_at = extract_remaining_and_expire(driver)
        log(f"📦 剩余流量：{remaining} | ⏳ 到期时间：{expire_at}")

        # 8) 查找签到按钮
        log("🔍 查找签到按钮")
        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = (sign_btn.text or "").strip()
        log(f"📌 签到按钮文字：{btn_text}")

        # 9) 已签到（网页层面）
        if ("已" in btn_text) or ("成功" in btn_text) or ("签到" not in btn_text and len(btn_text) > 0):
            # 有些站会显示“已签到/已签”之类，这里按你之前逻辑
            append_signed_log(slot_name, "ALREADY", email_masked, remaining, expire_at, f"btn={btn_text}")
            msg = (
                f"✅ {slot_name} 已签到（网页检测）\n"
                f"账号：{email_masked}\n"
                f"状态：已签到\n"
                f"剩余流量：{remaining}\n"
                f"到期时间：{expire_at}"
            )
            log("🎉 今日已经签过到（网页检测）")
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
            # 按钮消失也可以认为成功（按你原逻辑）
            status = "SUCCESS"
            detail = "btn_disappeared"

        append_signed_log(slot_name, status, email_masked, remaining, expire_at, detail)

        if status == "SUCCESS":
            msg = (
                f"✅ {slot_name} 签到成功\n"
                f"账号：{email_masked}\n"
                f"状态：成功\n"
                f"剩余流量：{remaining}\n"
                f"到期时间：{expire_at}"
            )
        else:
            msg = (
                f"⚠️ {slot_name} 签到状态未知\n"
                f"账号：{email_masked}\n"
                f"状态：未知\n"
                f"剩余流量：{remaining}\n"
                f"到期时间：{expire_at}\n"
                f"说明：{detail}"
            )

        tg_notify(msg)
        log(f"✅ 流程结束：{status}")

    except Exception as e:
        save_screen(driver, "ERROR")
        # 尝试在异常时也抓一次页面信息（可能抓不到）
        remaining, expire_at = "-", "-"
        try:
            remaining, expire_at = extract_remaining_and_expire(driver)
        except:
            pass

        append_signed_log(slot_name, "ERROR", email_masked, remaining, expire_at, f"{type(e).__name__}: {e}")

        msg = (
            f"❌ {slot_name} 签到失败\n"
            f"账号：{email_masked}\n"
            f"状态：失败\n"
            f"剩余流量：{remaining}\n"
            f"到期时间：{expire_at}\n"
            f"错误：{type(e).__name__}: {e}"
        )
        log(msg)
        tg_notify(msg)

    finally:
        try:
            driver.quit()
        except:
            pass
        log("🛑 脚本结束")

if __name__ == "__main__":
    main()
