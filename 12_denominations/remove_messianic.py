import csv

import os 
import psycopg2
from itertools import chain
from dotenv import load_dotenv

load_dotenv()
PRIORITY_KEYWORDS = (
    "about", "who-we-are", "who_we_are", "whoweare", "belief", "denomination",
    "affiliat", "congregation", "welcome", "mission", "clergy", "rabbi",
    "our-", "history", "membership", "join",
)
def test_string(s):
    if 'messianic' in s.lower() or 'yeshua' in s.lower() or 'yashua' in s.lower():
        return True
    return False

def test_chabad(s):
    if 'chabad' in s.lower() or 'lubavitch' in s.lower():
        return True
    return False
def test_urls(urls, data):
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor()
    for url, i in urls:
        cur.execute("""
            SELECT content_text FROM web_pages, websites WHERE websites.id = web_pages.website_id AND websites.url = %s
        """, (url,))
        results = str(chain.from_iterable(cur.fetchall()))

        if test_string(results):
            data[i][3] = "Messianic Judaism"
            data[i][4] = "automated content search"
        if test_chabad(results):
            data[i][3] = "Chabad"
            data[i][4] = "automated content search"
    conn.close()

def main():
    print("Starting to test for Messianic Judaism.../n")
    url_to_test = []
    with open('12_denominations/denomination_review_sample2.csv', 'r') as f:
        reader = csv.reader(f)
        data = list(reader)
    for i,row in enumerate(data[1:], start=1):
        
        if row[3] == "" and row[4] == "":
            if test_string(row[0]) or test_string(row[2]):
                data[i][3] = "Messianic Judaism"
                data[i][4] = "automated name search"
            if test_chabad(row[0]) or test_chabad(row[2]):
                data[i][3] = "Chabad"
                data[i][4] = "automated name search"
            url_to_test.append((row[2], i))
    test_urls(url_to_test, data)         
    with open('denominations_review_sample_messianic_removed.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(data)
        print("Finished testing for Messianic Judaism and saved results to denominations_review_sample_messianic_removed.csv")
        
if __name__ == "__main__":
    main()