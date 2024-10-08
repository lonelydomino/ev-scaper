import os
import pandas as pd
import pyarrow as pa
import time

from datetime import datetime
from glob import glob
import pandas_gbq

from pathlib import Path
from selenium import webdriver
from chromedriver_py import binary_path
from selenium.common.exceptions import TimeoutException
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from tqdm import tqdm

import PySimpleGUI as sg


def init_driver(local_download_path):
    os.makedirs(local_download_path, exist_ok=True)
    
    # Set up the driver
    svc = webdriver.ChromeService(executable_path=binary_path)

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # better for docker/portal

    driver = webdriver.Chrome(service=svc, options=chrome_options)

    # ensure that any CSV downloads are saved to project dir, not default downloads folder
    driver.command_executor._commands["send_command"] = ("POST", '/session/$sessionId/chromium/send_command')
    params = {'cmd': 'Page.setDownloadBehavior', 'params': {'behavior': 'allow','downloadPath':local_download_path}}
    command_result = driver.execute("send_command", params)

    return driver

def submit_election(driver, homepage, election):

    # Open the webpage
    driver.get(homepage)
    
    # Wait for the dropdown to be loaded (change the wait time as necessary)
    wait = WebDriverWait(driver, 2)
    wait.until(EC.element_to_be_clickable((By.ID, 'idElection')))

    # Select the election from the dropdown by visible text
    dropdown_element = driver.find_element(By.ID, 'idElection')  # Updated to the new method
    select = Select(dropdown_element)

    select.select_by_visible_text(election)
    
    # Click the submit button
    submit_button = driver.find_element(By.XPATH, '//button[@onclick="return submitForm();"]')
    submit_button.click()
    return driver

def get_selected_ev_date_dropdown(driver, dropdown_name = 'Early Voting Turnout by Date'):
    wait = WebDriverWait(driver, 2)
    dropdown_container = wait.until(EC.visibility_of_element_located((By.XPATH, f"//div[contains(text(), '{dropdown_name}')]")))
    dropdown_element = dropdown_container.find_element(By.XPATH, "./following-sibling::div//select[@id='selectedDate']")
    select = Select(dropdown_element)
    return select

def get_report_dates(driver, origin_url, election):    
    # navigate to the election page to see dates of eligible reports
    driver = submit_election(driver, origin_url, election)
    # Locate the parent div by checking for unique text within, then find the dropdown within this div
    
    select = get_selected_ev_date_dropdown(driver)
    
    dates = [option.text.strip() for option in select.options][1:]  # w/o 'Select Early Voting Date'
    return dates

def get_elections(driver, homepage):
    elections = []
    # Open the webpage
    driver.get(homepage)
    
    # Wait for the dropdown to be loaded (change the wait time as necessary)
    wait = WebDriverWait(driver, 2)
    wait.until(EC.element_to_be_clickable((By.ID, 'idElection')))

    # Select the election from the dropdown by visible text
    dropdown_element = driver.find_element(By.ID, 'idElection')  # Updated to the new method
    select = Select(dropdown_element)

    for option in select.options: #Adds dropdown items to elections list
        elections.append(option.text)

    return elections

def get_partisanship(election):
    if 'DEMOCRATIC' in election:
        return 'DEMS'
    if 'REPUBLICAN' in election:
        return 'GOP'
    return 'N/A'

def download_election_data(driver, homepage, election):
    # Get report-dates we'll need to iterate through
    report_dates = get_report_dates(driver, ORIGIN_URL, ELECTION)
    num_csvs_downloaded = 0  # tracking total downloaded csvs lets us confirm each is downloaded
    final_df = pd.DataFrame()

    for d in tqdm(report_dates):
        print(f"Downloading report for {d}")
        # navigate back to the main Early Voter page for this election 
        driver = submit_election(driver, ORIGIN_URL, ELECTION)

        # Select current date from dropdown
        select = get_selected_ev_date_dropdown(driver)
        select.select_by_visible_text(d)

        # Click the submit button for fetching table of EV detailed data
        time.sleep(3)
        driver.execute_script("validateSubmit();")

        # Click the "Generate Report" button to download as a csv
        # ...unless we got a pop-up saying there's no data for this date
        print(f"Executing downloadReport() button / js script")
        DOWNLOAD_WAIT_SECONDS = 5
        try:
            driver.execute_script("downloadReport('');")
            WebDriverWait(driver, DOWNLOAD_WAIT_SECONDS).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            print(f"Alert text: {alert.text}")
            alert.accept()
        except TimeoutException:
            print(f"No alert found after {DOWNLOAD_WAIT_SECONDS} seconds; attempting to process file download")

            # Wait for the download to complete
            num_csvs_downloaded += 1
            while len([f for f in os.listdir(CSV_DL_DIR) if f.endswith('.csv')]) < num_csvs_downloaded:
                print(f"waiting for {d} to download...")
                time.sleep(1)

            # read that latest-downloaded csv into a df; append to results
            csv_files = [f for f in os.listdir(CSV_DL_DIR) if f.endswith('.csv')]
            cwd = os.getcwd()  # Get the current working directory (cwd)
            latest_file = max(csv_files, key=lambda x: os.path.getctime(os.path.join(CSV_DL_DIR, x)))   

            # not including all columns here; just the ones that seem like they might get mistaken for ints (but shouldn't be)
            # Adds a file date column to the CSV
            dtypes = {c:'string' for c in ['ID_VOTER', 'PRECINCT', 'POLL PLACE ID']}
            df = pd.read_csv(os.path.join(CSV_DL_DIR, latest_file), dtype_backend='pyarrow', dtype=dtypes)  
            df['filedate'] = datetime.strptime(d, "%B %d,%Y")
            final_df = pd.concat([final_df, df], axis=0, ignore_index=True)
           
           # Rename each file according to our naming convention
            if get_partisanship(election) == 'DEMS':
                os.rename(os.path.join(CSV_DL_DIR, latest_file), os.path.join(CSV_DL_DIR, "DEMS_" + str(datetime.strptime(d, '%B %d,%Y').strftime('%m_%d_%Y')) + '_EV.csv'))
            if get_partisanship(election) == 'GOP':       
                os.rename(os.path.join(CSV_DL_DIR, latest_file), os.path.join(CSV_DL_DIR, "GOP_" + str(datetime.strptime(d, '%B %d,%Y').strftime('%m_%d_%Y')) + '_EV.csv'))
            if get_partisanship(election) == 'N/A':
                os.rename(os.path.join(CSV_DL_DIR, latest_file), os.path.join(CSV_DL_DIR, str(datetime.strptime(d, '%B %d,%Y').strftime('%m_%d_%Y')) + '_EV.csv'))
            
            #unindent two levels; out of the try/except block and out of the for loop of dates
    print(f"uploading to GBQ: {GBQ_DEST_DATASET}.{GBQ_DEST_TABLENAME}")
    pandas_gbq.to_gbq(final_df, 
            f"{GBQ_DEST_DATASET}.{GBQ_DEST_TABLENAME}", 
            if_exists='replace',
            project_id='demstxsp')    
            
                
if __name__ == "__main__":

    # PARAMS (should be configurable)
    ELECTION = ''
   

    # Constants (these are not configurable, or at least there's no point in changing them)
    ORIGIN_URL = "https://earlyvoting.texas-election.com/Elections/getElectionDetails.do"
    CSV_DL_DIR = "downloaded_files"

    

    # initialize the driver (mainly to ensure CSVs we download stay in this project folder)    
    driver = init_driver(local_download_path=CSV_DL_DIR)

    # clear local downloads folder before beginning 
    # (later we use count of files in this folder to determine when a new file has finished 
    # downloading and is ready to be renamed; so we need to start with a clean slate)
    for f in os.listdir(CSV_DL_DIR):
        fpath = os.path.join(CSV_DL_DIR, f)
        if os.path.isfile(fpath):
            os.remove(fpath)

    layout = [  [sg.Text("What's your election?")],
                [sg.Combo(list(get_elections(driver, ORIGIN_URL)), size=(60,40), readonly=True, enable_events=False, key='_LIST_')],
                [sg.Button('Ok'), sg.Button('Cancel')] ]

    # Create the Window
    window = sg.Window('Election Scraper', layout)

    # Event Loop to process "events" and get the "values" of the inputs
    while True:
        event, values = window.read()

    # if user clicks ok
        if event == 'Ok':
            ELECTION = values['_LIST_']
            GBQ_DEST_DATASET = "evav_processing_2024"
            GBQ_DEST_TABLENAME = ELECTION.replace(" ", "_").lower()
            print(f" to GBQ: {GBQ_DEST_DATASET}.{GBQ_DEST_TABLENAME}")
            download_election_data(driver, ORIGIN_URL, ELECTION)

        
            break
    # if user closes window or clicks cancel
        if event == sg.WIN_CLOSED or event == 'Cancel':
            break

    window.close()
    
    
    

   
     

    
