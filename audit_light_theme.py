import time, os
from playwright.sync_api import sync_playwright

os.makedirs(r'C:\YAPF_Farm\shots\audit', exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    
    # 1. Desktop Light (1400x900)
    context = browser.new_context(viewport={'width': 1400, 'height': 900})
    page = context.new_page()
    page.goto('http://127.0.0.1/login')
    page.fill('input[type="text"]', 'admin')
    page.fill('input[type="password"]', 'pre12345')
    page.click('button[type="submit"]')
    page.wait_for_load_state('networkidle')
    time.sleep(1)

    # Switch to Light Theme
    page.evaluate('''() => {
        localStorage.setItem('yapf_theme', 'light');
        document.documentElement.classList.remove('dark');
        document.documentElement.classList.add('light');
    }''')
    page.reload()
    page.wait_for_load_state('networkidle')
    time.sleep(1)

    # 1a. Overview
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\01_overview.png')

    # 1b. Projects
    page.click(r'aside button[\@click*="projects"]')
    time.sleep(1)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\02_projects.png')

    # Modal: Add Project
    page.click(r'button[\@click*="openAddProject"]')
    time.sleep(0.5)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\02b_add_project_modal.png')
    # Close modal using button
    page.click(r'button[\@click*="showAddProject = false"]')
    time.sleep(0.5)

    # 1c. Positions
    page.click(r'aside button[\@click*="positions"]')
    time.sleep(1)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\03_positions.png')

    # 1d. Logs
    page.click(r'aside button[\@click*="logs"]')
    time.sleep(1)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\04_logs.png')

    # 1e. Settings - Captcha
    page.click(r'aside button[\@click*="settings"]')
    time.sleep(1)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\05_settings_captcha.png')

    # Settings - Proxy
    page.click(r'button[\@click*="proxy"]')
    time.sleep(0.5)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\06_settings_proxy.png')

    # Settings - Telegram
    page.click(r'button[\@click*="telegram"]')
    time.sleep(0.5)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\07_settings_telegram.png')

    # 1f. Manual
    page.click(r'aside button[\@click*="manual"]')
    time.sleep(1)
    page.screenshot(path=r'C:\YAPF_Farm\shots\audit\08_manual.png')

    # 2. Mobile Light (390x844)
    m_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    m_page = m_context.new_page()
    m_page.goto('http://127.0.0.1/login')
    m_page.fill('input[type="text"]', 'admin')
    m_page.fill('input[type="password"]', 'pre12345')
    m_page.click('button[type="submit"]')
    m_page.wait_for_load_state('networkidle')
    time.sleep(1)
    m_page.evaluate('''() => {
        localStorage.setItem('yapf_theme', 'light');
        document.documentElement.classList.remove('dark');
        document.documentElement.classList.add('light');
    }''')
    m_page.reload()
    m_page.wait_for_load_state('networkidle')
    time.sleep(1)

    m_page.screenshot(path=r'C:\YAPF_Farm\shots\audit\09_mobile_overview.png')

    # Open Mobile Drawer
    m_page.click(r'header button[\@click*="mobileNavOpen"]')
    time.sleep(0.5)
    m_page.screenshot(path=r'C:\YAPF_Farm\shots\audit\10_mobile_drawer.png')

    browser.close()
    print("AUDIT SCREENSHOTS COMPLETED!")
