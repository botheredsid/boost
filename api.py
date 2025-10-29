import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

@app.get("/browser")
def browser_test():
    chrome_path = os.environ.get("GOOGLE_CHROME_SHIM", "/usr/bin/chromium-browser")

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    service = Service(chrome_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.get("https://www.google.com")
    title = driver.title
    driver.quit()
    return {"page_title": title}
