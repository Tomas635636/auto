# Neworld 自动签到脚本（最终可用版）

import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://neworld.tv/auth/login"
USER_CENTER_URL = "https://neworld.tv/user"

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

def main():
    driver = init_chrome()

    username = os.environ.get("USERNAME", "").strip()
    password = os.environ.get("PASSWORD", "").strip()

    if not username or not password:
        print("❌ 未获取到账号或密码，请检查 Secrets 里的 USERNAME / PASSWORD")
        driver.quit()
        return

    print(f"📌 开始执行签到，账号：{username}")

    try:
        # 1. 打开登录页
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        driver.save_screenshot("1_登录页.png")
        print("✅ 打开登录页")

        # 2. 输入账号密码
        email_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "email"))
        )
        pwd_input = driver.find_element(By.ID, "passwd")

        email_input.clear()
        email_input.send_keys(username)
        pwd_input.clear()
        pwd_input.send_keys(password)

        driver.save_screenshot("2_已输入账号密码.png")
        print("✅ 已输入账号密码")

        # 3. 点击登录按钮（注意：ID 是 login-dashboard）
        login_btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "login-dashboard"))
        )
        login_btn.click()

        # 等待跳转离开登录页
        WebDriverWait(driver, 30).until(lambda d: "/auth/login" not in d.current_url)
        time.sleep(2)
        driver.save_screenshot("3_登录成功.png")
        print("✅ 登录成功，当前URL：", driver.current_url)

        # 4. 进入用户中心
        driver.get(USER_CENTER_URL)
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        driver.save_screenshot("4_用户中心.png")
        print("✅ 已进入用户中心")

        # 5. 定位签到按钮（关键：ID = check-in）
        sign_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "check-in"))
        )

        print("📌 找到签到按钮，文本：", sign_btn.text)

        # 滚动到可视区域
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sign_btn)
        time.sleep(0.5)

        # 6. 点击签到
        sign_btn.click()
        time.sleep(3)
        driver.save_screenshot("5_点击签到后.png")
        print("🎉 已点击签到按钮")

        # 7. 验证状态
        try:
            sign_btn_after = driver.find_element(By.ID, "check-in")
            txt = sign_btn_after.text.strip()
            print("📌 当前按钮文字：", txt)
            if "已" in txt:
                print("🎉 签到成功！")
        except:
            print("🎉 签到成功（按钮已消失）")

    except Exception as e:
        try:
            driver.save_screenshot("99_错误.png")
        except:
            pass
        print("❌ 执行出错：", e)

    finally:
        driver.quit()
        print("🔚 脚本执行结束")

if __name__ == "__main__":
    main()
