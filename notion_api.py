import requests, json
import csv

def readDatabase(database_id, headers):
    read_url = "https://api.notion.com/v1/databases/"+database_id
    res = requests.request("GET", read_url, headers=headers)
    data = res.json()
    print(res.status_code)
    #print(res.text)
    with open('./db.json', 'w', encoding = 'utf8') as f:
        json.dump(data, f, ensure_ascii = False)

def queryPage(database_id, headers):
    query_url = "https://api.notion.com/v1/databases/"+database_id+"/query"
    res = requests.request("POST", query_url, headers=headers)
    data = res.json()
    print(res.status_code)
    with open('./db.json', 'w', encoding = 'utf8') as f:
        json.dump(data, f, ensure_ascii = False)

def createPage(database_id, headers):
    create_url = "https://api.notion.com/v1/pages"
    fname = 'newpage.json'
    with open(fname, 'r') as f:
        newPageData = json.load(f)
    newPageData['parent']['database_id'] = database_id
    data = json.dumps(newPageData)
    res = requests.request("POST", create_url, headers=headers, data=data)
    print(res.status_code)
    #print(res.text)