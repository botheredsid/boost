import os
from fastapi import FastAPI
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

@app.get("/browser")
def browser_test():
    chrome_path = "/usr/bin/chromium"
    driver_path = "/usr/bin/chromedriver"

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.get("https://www.google.com")
    title = driver.title
    driver.quit()
    return {"page_title": title}
