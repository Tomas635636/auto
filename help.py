# ============================================
# Neworld 多账号自动签到脚本（终极稳定安全版）
#
# 功能：
#  - 支持 4 个 slot（SLOT1~SLOT4），由 GitHub Actions 注入 SLOT_NAME
#  - 每个 slot 使用一个固定标记文件：SIGNED_SLOT?.txt
#  - 文件内记录：签到时间 / 剩余流量 / 到期时间
#  - 多次触发时如果检测到【今天已签到】→ 直接退出（不登录）
#  - Telegram 通知（显示脱敏邮箱）
#  - 日志 + 截图
#  - 失败自动截图
# ============================================

import os
import time
import logging
import requests
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ========== 基本配置 ==========
LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

LOG_FILE = "run.log"

# 使用北京时间
TZ = ZoneInfo("Asia/Shanghai")

# ========== 日志系统 ==========
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

# ========== Telegram 通知 ==========
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_notify(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=10)
    except Exception:
        pass

# ========== 工具函数 ==========
def now_bj() -> datetime:
    return datetime.now(TZ)

def today_bj() -> date:
    return now_bj().date()

def mask_email(email):
    """邮箱脱敏显示：ab***@gmail.com"""
    if not email or "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        return name[0] + "***@" + domain
    return name[:2] + "***@" + domain

# ========== 标记文件 ==========
def mark_filename(slot_name: str) -> str:
    return f"SIGNED_{slot_name}.txt"

def read_mark_if_signed_today(slot_name: str) -> bool:
    """
    如果标记文件存在，且 signed_at 是【今天（北京时间）】，返回 True
    """
    fn = mark_filename(slot_name)
    if not os.path.exists(fn):
        return False

    try:
        kv = {}
        with open(fn, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()

        if kv.get("status") != "OK":
            return False

        signed_at = kv.get("signed_at", "")
        dt = datetime.strptime(signed_at, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=TZ)

        return dt.date() == today_bj()

    except Exception as e:
        log(f"⚠️ 标记文件解析失败（将按未签到处理）：{e}")
        return False

def write_mark_ok(slot_name: str, username: str, traffic: str, expire: str):
    fn = mark_filename(slot_name)
    t = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    content = "\n".join([
        "status=OK",
        f"slot={slot_name}",
        f"user={username}",
        f"signed_at={t}",
        "tz=Asia/Shanghai",
        f"traffic_left={traffic}",
        f"expire_at={expire}",
        "",
    ])
    with open(fn, "w", encoding="utf-8") as f:
        f.write(content)

    log(f"📝 写入签到标记：{fn}（{t}）")

# ========== Chrome ==========
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
        filename = f"{now_bj().strftime('%Y%m%d_%H%M%S')}_{name}.png"
        driver.save_screenshot(filename)
        log(f"📸 已保存截图: {filename}")
    except:
        pass

# ========== 抓取流量和到期时间 ==========
def get_traffic_and_expire(driver):
    traffic = "UNKNOWN"
    expire = "UNKNOWN"

    try:
        el = driver.find_element(By.XPATH, "//span[contains(text(),'剩余流量')]")
        txt = el.text.strip()
        m = re.search(r'([\d\.]+\s*[A-Z]+)', txt)
        if m:
            traffic = m.group(1)
    except:
        pass

    try:
        el2 = driver.find_element(By.XPATH, "//p[contains(text(),'到期')]")
        txt2 = el2.text.strip()
        m2 = re.search(r'\((.*?)\)', txt2)
        if m2:
            expire = m2.group(1)
    except:
        pass

    return traffic, expire

# ========== 主流程 ==========
def main():
    slot_name = os.environ.get("SLOT_NAME", "").strip()
    if not slot_name:
        log("❌ 未获取到 SLOT_NAME")
        return

    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    safe_user = mask_email(username)

    log(f"🚀 启动自动签到 | 北京时间={now_bj().strftime('%Y-%m-%d %H:%M:%S')} | slot={slot_name} | user={safe_user}")

    # ===== 已签检测（跨运行记忆）=====
    if read_mark_if_signed_today(slot_name):
        msg = f"🛑 {safe_user} 今日已签到（标记文件），跳过执行"
        log(msg)
        tg_notify(msg)
        return

    if not username or not password:
        log("❌ 未获取到账号或密码")
        return

    driver = init_chrome()

    try:
        # 打开登录页
        log("🌐 打开登录页")
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        save_screen(driver, "login_page")

        # 输入账号密码
        log("✍️ 输入账号密码")
        email_input = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "email")))
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(username)
        pwd_input.clear()
        pwd_input.send_keys(password)
        save_screen(driver, "filled_form")

        # 登录
        log("🔐 点击登录按钮")
        login_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "login-dashboard")))
        login_btn.click()

        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        save_screen(driver, "after_login")
        log("✅ 登录成功")

        # 进入用户中心
        log("🏠 进入用户中心")
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        save_screen(driver, "user_center")

        # 读取流量和到期时间
        traffic, expire = get_traffic_and_expire(driver)
        log(f"📊 剩余流量: {traffic}")
        log(f"⏳ 到期时间: {expire}")

        # 查找签到按钮
        log("🔍 查找签到按钮")
        sign_btn = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "check-in")))
        btn_text = sign_btn.text.strip()
        log(f"📌 按钮文字：{btn_text}")

        # 如果网页显示已签到
        if "已" in btn_text or "成功" in btn_text:
            msg = f"ℹ️ {safe_user} 今日已签到\n📊 剩余流量: {traffic}\n⏳ 到期时间: {expire}"
            log(msg)
            write_mark_ok(slot_name, username, traffic, expire)
            tg_notify(msg)
            return

        # 执行签到
        log("🖱️ 点击签到按钮")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(1)
        sign_btn.click()
        time.sleep(3)
        save_screen(driver, "after_click")

        # 再次检测
        try:
            sign_btn2 = driver.find_element(By.ID, "check-in")
            new_text = sign_btn2.text.strip()
            log(f"📌 点击后按钮文字：{new_text}")

            if "已" in new_text or "成功" in new_text:
                msg = f"✅ {safe_user} 签到成功\n📊 剩余流量: {traffic}\n⏳ 到期时间: {expire}"
                log(msg)
                write_mark_ok(slot_name, username, traffic, expire)
                tg_notify(msg)
            else:
                msg = f"⚠️ {safe_user} 签到状态未知\n📊 剩余流量: {traffic}\n⏳ 到期时间: {expire}"
                log(msg)
                tg_notify(msg)

        except:
            msg = f"✅ {safe_user} 签到成功（按钮消失）\n📊 剩余流量: {traffic}\n⏳ 到期时间: {expire}"
            log(msg)
            write_mark_ok(slot_name, username, traffic, expire)
            tg_notify(msg)

    except Exception as e:
        err = f"❌ {safe_user} 执行异常：{e}"
        log(err)
        save_screen(driver, "ERROR")
        tg_notify(err)

    finally:
        driver.quit()
        log("🛑 脚本结束")

if __name__ == "__main__":
    main()
