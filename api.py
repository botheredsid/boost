# app.py
import asyncio
import base64
import time
import traceback
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ----- Configurable defaults -----
DEFAULT_WAIT_TIME = 25
DEFAULT_SCROLL_PAUSE = 1.0
DEFAULT_MAX_SCROLL_LOOPS = 60
DEFAULT_JS_POLL_INTERVAL = 1.0
DEFAULT_JS_POLL_TIMEOUT = 45
DEFAULT_PER_LISTING_WAIT = 3
# ---------------------------------

app = FastAPI(title="AffordableHousing Boost API", version="1.0")


class BoostRequest(BaseModel):
    email: str
    password: str
    num_buttons: int = Field(1, ge=1)
    headless: bool = True
    wait_time: Optional[int] = DEFAULT_WAIT_TIME


class BoostResponse(BaseModel):
    success: bool
    clicked_count: int
    clicked_addresses: List[Optional[str]]
    debug_logs: List[str]
    error: Optional[str] = None
    screenshot_base64: Optional[str] = None


# ---- Helper functions used inside worker ----
def get_element_text_via_js(drv, el):
    try:
        txt = drv.execute_script(
            "return (arguments[0].innerText || arguments[0].textContent || '').trim();", el
        )
        return (txt or "").strip()
    except Exception:
        return ""


def find_address_for_button(drv, btn):
    """Best-effort: try listing--card ancestor, fallback to preceding address."""
    try:
        # 1) listing--card ancestor
        try:
            anc = btn.find_element(By.XPATH, "./ancestor::div[contains(@class,'listing--card')][1]")
            addr_el = anc.find_element(By.CSS_SELECTOR, "div.listing--property--address span, div.listing--property--address")
            addr = get_element_text_via_js(drv, addr_el)
            if addr:
                return addr
        except Exception:
            pass

        # 2) other ancestors
        for xp in [
            "./ancestor::div[contains(@class,'listing--item')][1]",
            "./ancestor::div[contains(@class,'listing--property--wrapper')][1]",
        ]:
            try:
                anc = btn.find_element(By.XPATH, xp)
                addr_el = anc.find_element(By.CSS_SELECTOR, "div.listing--property--address span, div.listing--property--address")
                addr = get_element_text_via_js(drv, addr_el)
                if addr:
                    return addr
            except Exception:
                pass

        # 3) preceding address in DOM
        try:
            addr_el = btn.find_element(By.XPATH, "preceding::div[contains(@class,'listing--property--address')][1]//span")
            addr = get_element_text_via_js(drv, addr_el)
            if addr:
                return addr
        except Exception:
            pass
    except Exception:
        pass
    return None


def safe_js_count(drv, selector):
    try:
        return int(drv.execute_script(f"return document.querySelectorAll('{selector}').length || 0;"))
    except Exception:
        return 0


def js_count_in_iframes(drv, selector):
    counts = []
    try:
        frames = drv.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        frames = []
    for i, f in enumerate(frames):
        try:
            drv.switch_to.frame(f)
            c = safe_js_count(drv, selector)
            counts.append((i, c))
            drv.switch_to.default_content()
        except Exception:
            try:
                drv.switch_to.default_content()
            except Exception:
                pass
            counts.append((i, "err"))
    return counts


# ---- Selenium worker that performs the full flow ----
def selenium_boost_worker(email: str, password: str, num_buttons: int, headless: bool,
                          wait_time: int = DEFAULT_WAIT_TIME) -> BoostResponse:
    logs: List[str] = []
    clicked_addresses: List[Optional[str]] = []
    screenshot_b64 = None
    driver = None
    try:
        logs.append("Starting Selenium worker")

        # Chrome options - suitable for servers
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")  # use new headless mode when available
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=en-US")

        # Create driver (Selenium Manager will handle chromedriver)
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, wait_time)

        # --- LOGIN FLOW ---
        driver.get("https://www.affordablehousing.com/")
        logs.append("Opened affordablehousing.com")

        # click homepage sign-in
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.ah--signin--link"))).click()
        logs.append("Clicked homepage Sign In")

        email_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input#ah_user")))
        email_input.clear()
        email_input.send_keys(email)
        logs.append("Entered email")

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button#signin-button"))).click()
        logs.append("Clicked first Sign In button")

        password_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input#ah_pass")))
        password_input.clear()
        password_input.send_keys(password)
        logs.append("Entered password")

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button#signin-with-password-button"))).click()
        logs.append("Clicked final Sign In button")

        # wait for redirect (dashboard)
        wait.until(EC.url_contains("dashboard"))
        logs.append("Login confirmed (dashboard)")

        # --- NAVIGATE TO LISTING PAGE ---
        listing_url = "https://www.affordablehousing.com/v4/pages/Listing/Listing.aspx"
        driver.get(listing_url)
        logs.append(f"Navigated to {listing_url}")

        # incremental scrolling to load dynamic content
        SCROLL_PAUSE = DEFAULT_SCROLL_PAUSE
        MAX_SCROLL_LOOPS = DEFAULT_MAX_SCROLL_LOOPS
        loops = 0
        last_height = driver.execute_script("return document.body.scrollHeight")
        while loops < MAX_SCROLL_LOOPS:
            driver.execute_script("window.scrollBy(0, window.innerHeight);")
            time.sleep(SCROLL_PAUSE)
            loops += 1
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                time.sleep(0.5)
                break
            last_height = new_height
        logs.append(f"Finished incremental scrolling ({loops} loops)")

        # JS polling for presence of cards/buttons
        start = time.time()
        found_context = None
        found_count = 0
        while time.time() - start < DEFAULT_JS_POLL_TIMEOUT:
            c_buttons = safe_js_count(driver, "button.usage-boost-button, button.cmn--btn.usage-boost-button")
            c_cards = safe_js_count(driver, "div.listing--card, div.listing--property--wrapper, div.listing--item")
            c_addresses = safe_js_count(driver, "div.listing--property--address, div.listing--property--address span")
            logs.append(f"[JS POLL] buttons={c_buttons}, cards={c_cards}, addresses={c_addresses}")
            if c_buttons > 0 or c_cards > 0 or c_addresses > 0:
                found_context = ("main", None)
                found_count = max(c_buttons, c_cards, c_addresses)
                break

            # check iframes
            iframe_counts = js_count_in_iframes(driver, "button.usage-boost-button, button.cmn--btn.usage-boost-button")
            for idx, count in iframe_counts:
                if isinstance(count, int) and count > 0:
                    found_context = ("iframe", idx)
                    found_count = count
                    break
            if found_context:
                break

            time.sleep(DEFAULT_JS_POLL_INTERVAL)

        logs.append(f"[JS POLL RESULT] found_context={found_context} found_count={found_count}")
        if not found_context:
            # take screenshot for diagnostics
            try:
                screenshot_b64 = driver.get_screenshot_as_base64()
                logs.append("No cards/buttons found - saved screenshot (base64)")
            except Exception:
                logs.append("No cards/buttons found - screenshot failed")
            raise Exception("No listing cards or buttons found after JS polling")

        # collect buttons
        buttons = []
        if found_context[0] == "main":
            buttons = driver.find_elements(By.CSS_SELECTOR, "button.usage-boost-button, button.cmn--btn.usage-boost-button")
            logs.append(f"Collected {len(buttons)} button elements in main document (selenium)")
        else:
            idx = found_context[1]
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            if idx < len(frames):
                try:
                    driver.switch_to.frame(frames[idx])
                    buttons = driver.find_elements(By.CSS_SELECTOR, "button.usage-boost-button, button.cmn--btn.usage-boost-button")
                    logs.append(f"Collected {len(buttons)} buttons inside iframe #{idx}")
                    driver.switch_to.default_content()
                except Exception as e:
                    logs.append(f"Error collecting buttons from iframe #{idx}: {e}")
                    driver.switch_to.default_content()
                    buttons = []

        # fallback to JS collection if Selenium returns none
        if not buttons:
            try:
                buttons = driver.execute_script("return Array.from(document.querySelectorAll('button.usage-boost-button, button.cmn--btn.usage-boost-button'));")
                logs.append(f"Collected {len(buttons)} button elements via JS->Selenium")
            except Exception:
                buttons = []
                logs.append("Failed JS->Selenium button collection")

        logs.append(f"[DEBUG] total button WebElements collected: {len(buttons)}")

        # filter boostable: contains 'boost' and not in-progress
        boostable = []
        for idx, btn in enumerate(buttons, start=1):
            try:
                btn_text = get_element_text_via_js(driver, btn).lower()
                norm = " ".join(btn_text.split())
                classes = (btn.get_attribute("class") or "").lower()
                in_progress = ("usage-boost-inprogress" in classes) or ("progress" in norm) or ("inprogress" in norm)
                if ("boost" in norm) and not in_progress:
                    address = find_address_for_button(driver, btn)
                    boostable.append((address, btn, norm))
                    logs.append(f"[FOUND] btn#{idx} text='{norm}' addr='{address or '<none>'}'")
                else:
                    logs.append(f"[SKIP] btn#{idx} text='{norm[:60]}' class='{classes[:80]}' in_progress={in_progress}")
            except Exception as e:
                logs.append(f"[WARN] error inspecting btn#{idx}: {e}")
                continue

        logs.append(f"Total boostable detected: {len(boostable)}")
        if not boostable:
            # screenshot for debugging
            try:
                screenshot_b64 = driver.get_screenshot_as_base64()
                logs.append("No boostable buttons after filtering - saved screenshot (base64)")
            except Exception:
                logs.append("No boostable buttons - screenshot failed")
            raise Exception("No boostable buttons found to click")

        # click up to requested number
        to_click = min(num_buttons, len(boostable))
        clicked = 0
        for i in range(to_click):
            address, btn, text = boostable[i]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.6)
                driver.execute_script("arguments[0].click();", btn)
                clicked += 1
                clicked_addresses.append(address)
                logs.append(f"Clicked boost for: {address or '<address not found>'} (text='{text}')")
                time.sleep(1.5)
            except Exception as ce:
                logs.append(f"Error clicking boost #{i+1} ({address or 'unknown'}): {ce}")
                continue

        return BoostResponse(
            success=True,
            clicked_count=clicked,
            clicked_addresses=clicked_addresses,
            debug_logs=logs,
            error=None,
            screenshot_base64=screenshot_b64
        )

    except Exception as exc:
        tb = traceback.format_exc()
        logs.append(f"Unhandled exception: {str(exc)}")
        logs.append(tb)
        # try to capture screenshot
        try:
            if driver:
                screenshot_b64 = driver.get_screenshot_as_base64()
                logs.append("Captured error screenshot (base64)")
        except Exception:
            pass

        return BoostResponse(
            success=False,
            clicked_count=0,
            clicked_addresses=[],
            debug_logs=logs,
            error=str(exc),
            screenshot_base64=screenshot_b64
        )
    finally:
        try:
            if driver:
                driver.quit()
                logs.append("Driver.quit() called")
        except Exception:
            pass


# ---- FastAPI endpoint ----
@app.post("/boost", response_model=BoostResponse)
async def boost_endpoint(req: BoostRequest):
    # run Selenium in a background thread to avoid blocking event loop
    loop = asyncio.get_event_loop()
    try:
        result: BoostResponse = await loop.run_in_executor(
            None,
            selenium_boost_worker,
            req.email,
            req.password,
            req.num_buttons,
            req.headless,
            req.wait_time or DEFAULT_WAIT_TIME
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Server error: {exc}")

    return result


# if you want to run uvicorn programmatically:
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run("app:app", host="0.0.0.0", port=8000, log_level="info")
