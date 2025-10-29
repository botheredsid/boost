import os
import chromedriver_autoinstaller
from fastapi import FastAPI
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

@app.get("/browser")
def browser_test():
    # Install ChromeDriver automatically to a temporary path
    chromedriver_autoinstaller.install(path="/tmp")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--remote-debugging-port=9222")

    driver = webdriver.Chrome(options=chrome_options)
    driver.get("https://www.google.com")
    title = driver.title
    driver.quit()
    return {"page_title": title}
