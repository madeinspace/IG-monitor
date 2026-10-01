import json
import os
import random
import re
import select
import smtplib
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Resolve absolute paths & load environment variables
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
IG_BRAND_NAME = os.getenv("IG_BRAND_NAME", "IG")
IG_DASHBOARD_URL = os.getenv("IG_DASHBOARD_URL", "")
PROFILE = os.path.join(BASE_DIR, "browser-profile")
STATE_FILE = os.path.join(BASE_DIR, "assignments.json")

# --- HUMAN TIMING CONFIGURATION ---
MIN_DELAY = 55       # Minimum delay in seconds
MAX_DELAY = 65       # Maximum delay in seconds
ACTIVE_START_HOUR = 7   # 09:00 AM
ACTIVE_END_HOUR = 23    # 11:30 PM (23:30)

# --- SMTP CONFIGURATION ---
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
IG_EMAIL = os.getenv("IG_EMAIL")
IG_PASSWORD = os.getenv("IG_PASSWORD")

raw_recipients = os.getenv("RECIPIENT_EMAILS", "")
RECIPIENT_EMAILS = [r.strip() for r in raw_recipients.split(",") if r.strip()]

# --- TELEGRAM CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ANSI Color & Style Palette
C_RESET   = "\033[0m"
C_BOLD    = "\033[1m"
C_DIM     = "\033[2m"
C_CYAN    = "\033[38;5;51m"
C_EMERALD = "\033[38;5;48m"
C_GREEN   = "\033[38;5;84m"
C_AMBER   = "\033[38;5;214m"
C_GRAY    = "\033[38;5;244m"

IGNORED_ICONS = {
    "sticky_note", "sticky_note_2", "arrows_more_up", "all_inclusive",
    "award_meal", "restaurant", "local_bar", "lock", "chevron_right",
    "schedule", "event", "place", "location_on",
}


def clear_screen() -> None:
    os.system("clear" if os.name != "nt" else "cls")


def is_active_hours() -> bool:
    """Checks if current local time is within typical active hours (07:00 - 23:30)."""
    now = datetime.now()
    if now.hour < ACTIVE_START_HOUR or now.hour > ACTIVE_END_HOUR:
        return False
    if now.hour == ACTIVE_END_HOUR and now.minute > 30:
        return False
    return True


def notify(title: str, message: str) -> None:
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def send_telegram_raw(message_body: str) -> bool:
    """Core helper to dispatch a Markdown message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_body,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"   {C_AMBER}▲ Failed to dispatch Telegram alert: {e}{C_RESET}")
        return False


def send_telegram_alert(assignments: list, is_startup: bool = False) -> None:
    """Send instantaneous push notification for assignments or startup baseline."""
    if is_startup:
        lines = [f"• *{item['venue']}* ({item['date']})" for item in assignments]
        assignment_summary = "\n".join(lines) if lines else "No active assignments found."
        body = (
            f"🤖 *{IG_BRAND_NAME} Monitor Active*\n"
            f"Baseline loaded: *{len(assignments)}* Victorian assignment(s) online.\n\n"
            + assignment_summary
            + f"\n\n👉 [Open Dashboard]({IG_DASHBOARD_URL})"
        )
    else:
        cards = []
        for item in assignments:
            card = (
                f"⚡ *NEW VIC ASSIGNMENT AVAILABLE*\n"
                f"🏛 *Venue:* {item['venue']}\n"
                f"📅 *Date:* {item['date']}\n"
                f"ℹ️ *Details:* {item['details']}"
            )
            cards.append(card)
        body = "\n\n".join(cards) + f"\n\n👉 [Claim on Dashboard]({IG_DASHBOARD_URL})"

    if send_telegram_raw(body):
        label = "Startup baseline" if is_startup else "Alert"
        print(f"   {C_GREEN}✓ Telegram {label} notification delivered.{C_RESET}")


def send_telegram_sleep_notice(entering: bool) -> None:
    """Sends entry/exit notification for overnight quiet hours."""
    now_str = datetime.now().strftime("%I:%M %p")
    if entering:
        body = (
            f"🌙 *{IG_BRAND_NAME} Monitor — Sleep Mode*\n"
            f"Outside active hours ({ACTIVE_START_HOUR:02d}:00–{ACTIVE_END_HOUR}:30).\n"
            f"Active polling paused at {now_str} to preserve account security. Resuming at {ACTIVE_START_HOUR:02d}:00 AM."
        )
    else:
        body = (
            f"☀️ *{IG_BRAND_NAME} Monitor — Resuming*\n"
            f"Active hours began at {now_str}.\n"
            f"Routine polling and real-time alerts are back online."
        )

    if send_telegram_raw(body):
        action = "Sleep" if entering else "Wake"
        print(f"\n   {C_GREEN}✓ Telegram {action} status notice delivered.{C_RESET}\n")


def send_email(subject: str, assignments: list, is_alert: bool = False) -> None:
    if not SMTP_USER or not SMTP_PASSWORD or not RECIPIENT_EMAILS:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{IG_BRAND_NAME} Monitor <{SMTP_USER}>"
    msg["To"] = ", ".join(RECIPIENT_EMAILS)

    lines = [f"• [{item['state']}] {item['date']} | {item['venue']} — {item['details']}" for item in assignments]
    assignment_text = "\n".join(lines) if lines else "No active assignments found."
    plain_text = (
        f"{'🚨 NEW OPPORTUNITY ALERT' if is_alert else 'STATUS REPORT'}\n\n"
        + assignment_text
        + f"\n\nClaim on dashboard: {IG_DASHBOARD_URL}"
    )

    if is_alert:
        cards_html = "".join([
            f"""
            <div style="background: #ffffff; border: 1.5px solid #10b981; border-left: 6px solid #10b981; border-radius: 8px; padding: 18px 20px; margin-bottom: 14px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.04);">
                <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px;">
                    <span style="font-size: 18px; font-weight: 700; color: #064e3b; letter-spacing: -0.2px;">{item['venue']}</span>
                    <span style="background: #d1fae5; color: #065f46; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 3px 8px; border-radius: 9999px;">{item['state']}</span>
                </div>
                <div style="font-size: 13px; font-weight: 600; color: #059669; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">
                    📅 {item['date']}
                </div>
                <div style="font-size: 14px; color: #374151; line-height: 1.5; background: #f9fafb; padding: 10px 14px; border-radius: 6px; border: 1px solid #f3f4f6;">
                    {item['details']}
                </div>
            </div>
            """
            for item in assignments
        ])

        html_content = f"""
        <html>
        <body style="margin: 0; padding: 24px; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
            <div style="max-width: 600px; margin: 0 auto;">
                <div style="background: #10b981; color: #ffffff; padding: 20px 24px; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0; font-size: 20px; font-weight: 700;">⚡ New Assignment Available</h1>
                    <p style="margin: 4px 0 0 0; font-size: 13px; opacity: 0.9;">Ready to claim on the dashboard right now.</p>
                </div>
                <div style="background: #ffffff; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 8px 8px;">
                    {cards_html}
                    <div style="text-align: center; margin-top: 24px;">
                        <a href="{IG_DASHBOARD_URL}" style="display: inline-block; background-color: #10b981; color: #ffffff; font-weight: 700; font-size: 15px; padding: 12px 28px; text-decoration: none; border-radius: 6px;">
                            Claim Assignment →
                        </a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        rows_html = "".join([
            f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 12px 14px; font-size: 12px; font-weight: 700; color: #475569;">{item['state']}</td>
                <td style="padding: 12px 14px; font-size: 13px; color: #334155; white-space: nowrap;">{item['date']}</td>
                <td style="padding: 12px 14px;">
                    <div style="font-weight: 600; color: #0f172a; font-size: 14px;">{item['venue']}</div>
                    <div style="color: #64748b; font-size: 12px; margin-top: 2px;">{item['details']}</div>
                </td>
            </tr>
            """
            for item in assignments
        ]) or '<tr><td colspan="3" style="padding: 16px 14px; color: #64748b;">No active assignments found.</td></tr>'

        html_content = f"""
        <html>
        <body style="margin: 0; padding: 24px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
            <div style="max-width: 640px; margin: 0 auto; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
                <div style="background: #0f172a; color: #ffffff; padding: 18px 24px;">
                    <h2 style="margin: 0; font-size: 17px; font-weight: 600;">{IG_BRAND_NAME} &mdash; Monitor Active</h2>
                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #94a3b8;">Baseline assignments: {len(assignments)} active listing(s).</p>
                </div>
                <div style="padding: 20px;">
                    <table style="width: 100%; border-collapse: collapse; text-align: left;">
                        <thead>
                            <tr style="background-color: #f1f5f9; border-bottom: 2px solid #cbd5e1;">
                                <th style="padding: 8px 14px; font-size: 11px; text-transform: uppercase; color: #64748b;">State</th>
                                <th style="padding: 8px 14px; font-size: 11px; text-transform: uppercase; color: #64748b;">Date</th>
                                <th style="padding: 8px 14px; font-size: 11px; text-transform: uppercase; color: #64748b;">Details</th>
                            </tr>
                        </thead>
                        <tbody>{rows_html}</tbody>
                    </table>
                </div>
            </div>
        </body>
        </html>
        """

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, RECIPIENT_EMAILS, msg.as_string())
    except Exception:
        pass


def save_assignments(assignments: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "updated": datetime.now().isoformat(),
                "assignments": assignments,
            },
            f,
            indent=2,
        )


def reset_to_vic_view(page) -> bool:
    """Ensures the 'Available Assignments' tab is active and VIC is filtered."""
    try:
        avail_btn = page.locator("button[data-page='available'], button:has-text('Available Assignments')").first
        avail_btn.wait_for(state="visible", timeout=12000)
        avail_btn.click()
        page.wait_for_timeout(1000)

        # Explicitly wait for available tab container to be unhidden
        page.locator("#igb-page-available").wait_for(state="visible", timeout=12000)

        # Apply VIC filter dropdown
        state_select = page.locator("select#igb-filter-state")
        if state_select.is_visible(timeout=3000):
            state_select.select_option("VIC")
            state_select.dispatch_event("change")
            page.wait_for_timeout(800)

        return True
    except Exception as e:
        print(f" {C_AMBER}▲ Tab navigation notice: {e}{C_RESET}")
        return False


def soft_refresh_view(page) -> None:
    """Emulates a human flipping between dashboard tabs rather than hard-reloading entire page."""
    try:
        my_btn = page.locator("button[data-page='my-assessments']").first
        if my_btn.is_visible(timeout=2000):
            my_btn.click()
            page.wait_for_timeout(random.randint(400, 800))

        reset_to_vic_view(page)
        page.wait_for_timeout(random.randint(500, 1000))
    except Exception:
        page.reload(wait_until="domcontentloaded", timeout=30000)
        reset_to_vic_view(page)


def inject_mock_dom_assignment(page) -> None:
    page.evaluate("""() => {
        const list = document.querySelector('.igb-assignment-list');
        if (!list) return;

        let header = list.querySelector('.igb-date-group-header');
        if (!header) {
            header = document.createElement('div');
            header.className = 'igb-date-group-header';
            header.innerText = 'FRIDAY, 11 SEPTEMBER';
            list.prepend(header);
        }

        const fakeCard = document.createElement('div');
        fakeCard.className = 'igb-list-item';
        fakeCard.setAttribute('data-state', 'VIC');
        fakeCard.setAttribute('data-availability', 'available');
        fakeCard.setAttribute('data-assessment-id', 'test-sim-telegram-999');

        fakeCard.innerHTML = `
            <div style="font-weight: 700; font-size: 15px; color: #10b981;">Simulation Grill Fitzroy</div>
            <div>Between 11 Sep and 25 Sep</div>
            <div>Chef's Tasting Menu • Pipeline Alert Test Simulation</div>
        `;

        header.insertAdjacentElement('afterend', fakeCard);
    }""")


def collect_assignments(page) -> dict:
    list_container = page.locator(".igb-assignment-list")
    try:
        list_container.wait_for(state="visible", timeout=12000)
    except Exception:
        reset_to_vic_view(page)
        list_container.wait_for(state="visible", timeout=10000)

    items = page.locator(".igb-assignment-list .igb-list-item")
    count = items.count()
    assignments = {}

    for i in range(count):
        item = items.nth(i)
        state = (item.get_attribute("data-state") or "").strip().upper()
        if state and state != "VIC":
            continue

        class_str = item.get_attribute("class") or ""
        classes = class_str.split()
        if "filtered-out" in classes or "igb-restricted" in classes:
            continue

        availability = (item.get_attribute("data-availability") or "").lower()
        if availability in {"unavailable", "claimed"}:
            continue

        aid = item.get_attribute("data-assessment-id") or f"gen-{i}"
        date_header = item.locator("xpath=preceding-sibling::div[contains(@class, 'igb-date-group-header')][1]")
        date_str = date_header.inner_text().strip() if date_header.count() > 0 and date_header.is_visible() else "Date Pending"

        text_content = item.inner_text().strip()
        raw_lines = [l.strip() for l in text_content.splitlines() if l.strip()]
        lines = [l for l in raw_lines if l.lower() not in IGNORED_ICONS]

        venue = lines[0] if len(lines) > 0 else "Unknown Venue"
        details = " — ".join(lines[1:3]) if len(lines) > 1 else ""

        assignments[aid] = {
            "id": aid,
            "state": state or "VIC",
            "date": date_str,
            "venue": venue,
            "details": details,
        }

    return assignments


def refresh_and_scrape(page, inject_test: bool = False, is_hard_reload: bool = False) -> dict:
    if is_hard_reload:
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        reset_to_vic_view(page)
    else:
        soft_refresh_view(page)

    if inject_test:
        inject_mock_dom_assignment(page)
        page.wait_for_timeout(1000)

    return collect_assignments(page)


def ensure_authenticated_and_ready(page, is_headless: bool) -> None:
    if not IG_DASHBOARD_URL:
        raise RuntimeError("Set IG_DASHBOARD_URL in .env")
    page.goto(IG_DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    if "login" in page.url.lower() or page.locator("input[type='password']").count() > 0:
        password_input = page.locator("input[type='password']").first
        credentials_available = (
            IG_EMAIL
            and IG_PASSWORD
            and not IG_EMAIL.startswith("your_")
            and not IG_EMAIL.endswith("@example.com")
        )

        if credentials_available and password_input.count() > 0:
            login_form = password_input.locator("xpath=ancestor::form[1]")
            form_scope = login_form if login_form.count() > 0 else page
            email_input = form_scope.locator(
                "input[type='email'], input[autocomplete='username'], "
                "input[name*='email' i], input[id*='email' i], input[type='text']"
            ).first

            if email_input.count() > 0 and email_input.is_visible():
                email_input.fill(IG_EMAIL)
                password_input.fill(IG_PASSWORD)
                submit_button = form_scope.locator(
                    "button[type='submit'], input[type='submit']"
                ).first
                if submit_button.count() == 0:
                    submit_button = form_scope.get_by_role(
                        "button", name=re.compile(r"log\s*in|sign\s*in|continue", re.I)
                    ).first
                if submit_button.count() > 0:
                    submit_button.click()
                    page.wait_for_timeout(2000)
                    page.goto(IG_DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)

        still_on_login = (
            "login" in page.url.lower()
            or page.locator("input[type='password']").count() > 0
        )
        if still_on_login and is_headless:
            print(f"\n {C_AMBER}▲ Automatic login failed or needs additional verification.{C_RESET}")
            print(f"   Restart in visible mode to complete login, MFA, or CAPTCHA.\n")
            sys.exit(1)
        elif still_on_login:
            print(f" {C_AMBER}▲ Authentication required. Complete login in the browser window.{C_RESET}")
            input(f" {C_DIM}Press [ENTER] once you have logged in...{C_RESET}")
            page.goto(IG_DASHBOARD_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

    reset_to_vic_view(page)
    page.wait_for_timeout(1500)


def print_banner(is_headless: bool = None, test_mode: bool = False) -> None:
    clear_screen()
    title = f"{IG_BRAND_NAME.upper()} MONITOR"
    mode_str = "Configuring..." if is_headless is None else ("Faceless (Headless)" if is_headless else "Visible Window")
    test_str = f"  •  Test Hook: {'ON' if test_mode else 'OFF'}" if is_headless is not None else ""
    info = f"Region: VIC  •  Delay: {MIN_DELAY}-{MAX_DELAY}s (Jitter)  •  Mode: {mode_str}{test_str}"

    tg_status = "Active" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "Disabled"
    target = f"Email: {', '.join(RECIPIENT_EMAILS) or 'Off'}  •  Telegram: {tg_status}"

    inner_width = max(len(title), len(info), len(target)) + 4

    top    = f"{C_CYAN}╭" + ("─" * inner_width) + f"╮{C_RESET}"
    line1  = f"{C_CYAN}│  {C_BOLD}{title}{C_RESET}" + (" " * (inner_width - len(title) - 2)) + f"{C_CYAN}│{C_RESET}"
    line2  = f"{C_CYAN}│  {C_GRAY}{info}{C_RESET}"  + (" " * (inner_width - len(info) - 2))  + f"{C_CYAN}│{C_RESET}"
    line3  = f"{C_CYAN}│  {C_GRAY}{target}{C_RESET}"+ (" " * (inner_width - len(target) - 2))+ f"{C_CYAN}│{C_RESET}"
    bottom = f"{C_CYAN}╰" + ("─" * inner_width) + f"╯{C_RESET}"

    print(f"\n{top}\n{line1}\n{line2}\n{line3}\n{bottom}\n")


def print_table(assignments: list) -> None:
    if not assignments:
        print(f" {C_GRAY}No active assignments available currently.{C_RESET}\n")
        return

    print(f" {C_BOLD}Active Victorian Assignments ({len(assignments)} total){C_RESET}")
    print(f" {C_GRAY}───────────────────────────────────────────────────────────────────{C_RESET}")
    for item in assignments:
        print(f"  {C_EMERALD}◆{C_RESET} {C_BOLD}{item['venue']}{C_RESET}")
        print(f"    {C_CYAN}{item['date']}{C_RESET} {C_GRAY}│{C_RESET} {C_DIM}{item['details']}{C_RESET}")
    print(f" {C_GRAY}───────────────────────────────────────────────────────────────────{C_RESET}\n")


def check_for_enter() -> bool:
    readable, _, _ = select.select([sys.stdin], [], [], 0.0)
    if readable:
        sys.stdin.readline()
        return True
    return False


def timed_prompt(prompt_label: str, default_val: bool, timeout_sec: int = 10) -> bool:
    choice_str = "[Y/n]" if default_val else "[y/N]"
    default_text = "Y" if default_val else "N"

    for rem in range(timeout_sec, 0, -1):
        line = f" {C_BOLD}{prompt_label}{C_RESET} {choice_str} (Auto-{default_text} in {rem:02d}s): "
        sys.stdout.write(f"\r{line}")
        sys.stdout.flush()

        readable, _, _ = select.select([sys.stdin], [], [], 1.0)
        if readable:
            user_input = sys.stdin.readline().strip().lower()
            if not user_input:
                return default_val
            if default_val:
                return user_input not in ("n", "no", "0", "false")
            else:
                return user_input in ("y", "yes", "1", "true")

    sys.stdout.write(f"\r{prompt_label} {choice_str}: {default_text} (timeout)\n")
    sys.stdout.flush()
    return default_val


def ask_startup_options() -> tuple:
    print_banner(is_headless=None)
    print(f" {C_DIM}Startup options (press Enter for default):{C_RESET}\n")

    is_headless = timed_prompt("Run in faceless/background mode?", default_val=True, timeout_sec=10)
    test_mode = timed_prompt("Enable mock alert test on check #2?", default_val=False, timeout_sec=10)

    return is_headless, test_mode


def main():
    is_headless, test_mode = ask_startup_options()
    print_banner(is_headless, test_mode)

    if not IG_DASHBOARD_URL:
        raise RuntimeError("Set IG_DASHBOARD_URL in .env")

    with sync_playwright() as p:
        mode_label = f"{C_GRAY}faceless{C_RESET}" if is_headless else f"{C_AMBER}visible window{C_RESET}"
        print(f" {C_CYAN}⚡{C_RESET} Launching browser session ({mode_label})...")

        context = p.chromium.launch_persistent_context(
            PROFILE,
            headless=is_headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1440, "height": 950},
        )

        page = context.pages[0] if context.pages else context.new_page()
        ensure_authenticated_and_ready(page, is_headless)

        print(f" {C_CYAN}⚡{C_RESET} Syncing dashboard state & capturing baseline...")
        previous = refresh_and_scrape(page, inject_test=False, is_hard_reload=True)
        save_assignments(previous)

        # Refresh dashboard display
        print_banner(is_headless, test_mode)
        baseline_list = list(previous.values())
        print_table(baseline_list)

        # Baseline boot notifications
        send_email(
            subject=f"📋 {IG_BRAND_NAME} Baseline: {len(baseline_list)} Active VIC Assignment(s)",
            assignments=baseline_list,
            is_alert=False,
        )
        print(f" {C_GREEN}✓{C_RESET} Baseline email dispatched to {len(RECIPIENT_EMAILS)} recipient(s).")
        send_telegram_alert(baseline_list, is_startup=True)
        print()

        print(f" {C_DIM}Controls: Press [Enter] anytime to scan immediately. Press Ctrl+C to stop.{C_RESET}")
        print(f" {C_DIM}Window: Active polling between {ACTIVE_START_HOUR:02d}:00 and {ACTIVE_END_HOUR}:30 local time.{C_RESET}")
        if test_mode:
            print(f" {C_AMBER}Notice: Test injection armed (mock assignment will inject on check #2).{C_RESET}")
        print(f" {C_GRAY}───────────────────────────────────────────────────────────────────{C_RESET}")

        spinner_chars = ["◐", "◓", "◑", "◒"]
        spin_idx = 0
        scan_cycle = 0
        in_sleep_mode = False

        while True:
            try:
                active_now = is_active_hours()

                # Transition: Active -> Sleep Mode
                if not active_now and not in_sleep_mode:
                    in_sleep_mode = True
                    send_telegram_sleep_notice(entering=True)

                # Transition: Sleep Mode -> Active
                elif active_now and in_sleep_mode:
                    in_sleep_mode = False
                    send_telegram_sleep_notice(entering=False)

                # Overnight Sleep Pause
                if not active_now:
                    ts = datetime.now().strftime("%H:%M:%S")
                    status_line = f"  {C_AMBER}🌙{C_RESET} [{ts}] In sleep mode ({ACTIVE_START_HOUR:02d}:00–{ACTIVE_END_HOUR}:30). Sleeping for 15m...   "
                    sys.stdout.write(f"\r{status_line}")
                    sys.stdout.flush()
                    time.sleep(900)
                    continue

                manual_trigger = False

                # Dynamic Jitter Interval for this cycle
                cycle_delay = random.randint(MIN_DELAY, MAX_DELAY)

                # Countdown Ticker
                for remaining in range(cycle_delay, 0, -1):
                    if check_for_enter():
                        manual_trigger = True
                        break

                    ts = datetime.now().strftime("%H:%M:%S")
                    spin = spinner_chars[spin_idx % len(spinner_chars)]
                    spin_idx += 1

                    status_line = (
                        f"  {C_CYAN}{spin}{C_RESET} [{ts}] Watching {len(previous)} assignment(s) "
                        f"{C_GRAY}│{C_RESET} Next scan in {C_BOLD}{remaining:02d}s{C_RESET} "
                        f"{C_GRAY}(or press Enter){C_RESET}   "
                    )
                    sys.stdout.write(f"\r{status_line}")
                    sys.stdout.flush()
                    time.sleep(1)

                scan_cycle += 1
                should_inject = test_mode and (scan_cycle == 2)

                ts = datetime.now().strftime("%H:%M:%S")
                sys.stdout.write("\r" + " " * 80 + "\r")
                trigger_label = "Manual check triggered..." if manual_trigger else "Checking updates..."
                if should_inject:
                    trigger_label += f" {C_AMBER}[Injecting Test Card]{C_RESET}"
                sys.stdout.write(f"  {C_CYAN}⟳{C_RESET} [{ts}] {trigger_label}\r")
                sys.stdout.flush()

                # Soft refresh view; hard reload every ~10 checks
                is_hard = (scan_cycle % 10 == 0)
                current = refresh_and_scrape(page, inject_test=should_inject, is_hard_reload=is_hard)
                new_ids = set(current.keys()) - set(previous.keys())

                if new_ids:
                    sys.stdout.write("\r" + " " * 80 + "\r")
                    print(f"\n {C_EMERALD}{C_BOLD}⚡ [{ts}] {len(new_ids)} NEW CLAIMABLE ASSIGNMENT(S) DETECTED!{C_RESET}")

                    new_items_list = []
                    for aid in sorted(new_ids):
                        item = current[aid]
                        new_items_list.append(item)
                        print(f"   {C_EMERALD}▶{C_RESET} {C_BOLD}{item['venue']}{C_RESET} — {item['date']}")
                        print(f"     {C_DIM}{item['details']}{C_RESET}")

                        notify(
                            f"NEW VIC: {item['venue']}",
                            f"{item['date']} — {item['details']}"
                        )

                    subject = (
                        f"⚡ NEW VIC: {new_items_list[0]['venue']} ({new_items_list[0]['date']})"
                        if len(new_items_list) == 1
                        else f"⚡ {len(new_items_list)} NEW VIC Assignments Available!"
                    )

                    send_email(
                        subject=subject,
                        assignments=new_items_list,
                        is_alert=True,
                    )
                    print(f"   {C_GREEN}✓ Alert email dispatched.{C_RESET}")

                    send_telegram_alert(new_items_list, is_startup=False)
                    print()

                    previous = current
                    save_assignments(current)
                else:
                    previous = current

            except KeyboardInterrupt:
                sys.stdout.write("\r" + " " * 80 + "\r")
                print(f"\n {C_AMBER}■ Monitor stopped by user. Cleaning up...{C_RESET}\n")
                try:
                    context.close()
                except Exception:
                    pass  # browser/driver may already be gone
                break
            except Exception as e:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"\n {C_AMBER}▲ [{ts}] Notice: {e}{C_RESET}")


if __name__ == "__main__":
    main()