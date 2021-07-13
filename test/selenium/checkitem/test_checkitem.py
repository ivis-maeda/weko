# Generated by Selenium IDE
import time
import sys
import urllib.parse
import logging
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.chrome.options import Options

logging.basicConfig(level=logging.DEBUG, 
    filename="test.log",
    format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger(__name__)

rawUri = sys.argv[1]
rawUri = rawUri.replace('https://', '')
id = sys.argv[3]
pss = sys.argv[4]
pss = urllib.parse.quote(pss)

#class TestCheckitem():
options = Options()
#options.add_argument('--headless')
driver = webdriver.Chrome(executable_path='./chromedriver', options=options)
vars = {}

uri = ("https://" + id + ":" + pss + "@" + rawUri)

search = sys.argv[2]

#take screenshot
def takeScreenShot(file_name):
  time.sleep(30)
  os.makedirs('./ScreenShot/' + str(search) + '/', exist_ok=True)
  driver.save_screenshot('ScreenShot/' + str(search) + '/' + file_name + '.png')
  logger.info('save screenshot: ScreenShot/' + str(search) + '/' + file_name + '.png')

def cutUrl(cur_url):
  preIdx = cur_url.find('@')
  cuturl = cur_url[preIdx+1:]
  return "https://" + cuturl


driver.get(uri)
driver.set_window_size(1600, 1024)

time.sleep(5)
#check search
driver.find_element(By.ID, "q").click()
driver.find_element(By.ID, "q").send_keys(search)
driver.find_element(By.CSS_SELECTOR, ".fa-search").click()

takeScreenShot("search_page")

countText = driver.find_element_by_tag_name('ng-pluralize').text
preIdx = countText.find('of')
preSlice = countText[preIdx+2:]
sufIdx = preSlice.find('results')
sliceText = preSlice[:sufIdx-1]
logger.info("Count Search Results:" + sliceText)

#view landing page
time.sleep(3)
driver.find_element(By.CSS_SELECTOR, ".panel:nth-child(1) > .panel-heading .ng-binding").click()
logger.info("landing page URL:" + cutUrl(driver.current_url))
takeScreenShot("landing_page")

#metatag check

citation_abstract_url= driver.find_element(By.XPATH, "//meta[@name='citation_abstract_html_url']").get_attribute("content")

if citation_abstract_url ==  cutUrl(driver.current_url):
  logger.info("citation_abstract_html_url metatag is matching:" + cutUrl(driver.current_url))
else: 
  logger.error("citation_abstract_html_url metatag is not matching:" + cutUrl(driver.current_url)) 

#download check
dl_tbl = driver.find_elements_by_xpath("//div[@id='detail-item']/table/tbody/tr")
d = len(dl_tbl)
l = 1
while l <= d:
  driver.find_element(By.XPATH, "//div[@id='detail-item']/table/tbody/tr[" + str(l) + "]/td[4]/a/button").click()
  driver.refresh()
  l+=1

#export check
driver.find_element(By.CSS_SELECTOR, "li:nth-child(1) img").click()
takeScreenShot("export_JPCOAR")
driver.back()
driver.find_element(By.CSS_SELECTOR, "li:nth-child(2) img").click()
takeScreenShot("export_DublinCore")
driver.back()
driver.find_element(By.CSS_SELECTOR, "li:nth-child(3) img").click()
takeScreenShot("export_DDI")
driver.back()
driver.find_element(By.LINK_TEXT, "JSON").click()
takeScreenShot("export_JSON")
driver.back()
driver.find_element(By.LINK_TEXT, "BIBTEX").click()
takeScreenShot("export_DDI")
driver.back()

logger.info("check item end:" + cutUrl(driver.current_url))
driver.quit()