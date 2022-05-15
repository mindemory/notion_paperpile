import requests, json
import csv

def queryDatabase(database_id, headers):
    query_url = "https://api.notion.com/v1/databases/"+database_id+"/query"
    blurb = '{"start_cursor": null}'
    blurb = json.loads(blurb)

    res = requests.request("POST", query_url, headers=headers, data = blurb)
    data = res.json()
    print('Querying database: ' + str(res.status_code))
    return data

def queryDatabase_again(database_id, headers, next_cursor):
    query_url = "https://api.notion.com/v1/databases/"+database_id+"/query"
    
    cursor_val ={
        "start_cursor": next_cursor
    }
    cursor_val = json.dumps(cursor_val)

    res = requests.request("POST", query_url, headers=headers, data = cursor_val)
    data = res.json()
    print('Querying database: ' + str(res.status_code))
    return data

def createPage(database_id, headers, auth_year, journal, title):
    create_url = "https://api.notion.com/v1/pages"
    fname = 'newpage.json'
    with open(fname, 'r') as f:
        newPageData = json.load(f)
    newPageData['parent']['database_id'] = database_id
    newPageData['properties']['Journal']["rich_text"][0]['text']['content'] = journal
    newPageData['properties']['Journal']["rich_text"][0]['plain_text'] = journal
    newPageData['properties']['Title']["rich_text"][0]['text']['content'] = title
    newPageData['properties']['Title']["rich_text"][0]['plain_text'] = title
    newPageData['properties']['Author, Year']['title'][0]['text']['content'] = auth_year
    newPageData['properties']['Author, Year']['title'][0]['plain_text'] = auth_year
    data = json.dumps(newPageData)
    res = requests.request("POST", create_url, headers=headers, data=data)
    print('Creating page: ' + str(res.status_code))
    #print(res.text)