from pybtex.database.input import bibtex
import requests, json

from notion.client import NotionClient
import csv

with open("../notion_ids.csv", 'r') as f:
    f_csv = f.readlines()
database_id = f_csv[1]
secret = f_csv[2]
headers_read = {
    "Authorization": "Bearer " + secret,
    "Notion-Version": "2022-02-22"
}
headers_query = {
    "Authorization": "Bearer " + secret,
    "Notion-Version": "2022-02-22",
    "Content-Type": "application/json"
}

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
    pass
def updatePage(database_id, headers):
    pass
queryPage(database_id, headers_query)




#for row in f_csv:
#    print(row)
#print(next(f_csv))
#open a bibtex file
parser = bibtex.Parser()
bibdata = parser.parse_file("paperpile_references.bib")

# tralsator for removing punctuation
#translator = str.maketrans('', '', string.punctuation)

# open our output file
#f = open('my_bib.csv', 'w') 

# header row
#f.write("title\t year\t author\t journal\n")

#loop through the individual references
#print(bibdata)
#print(len(bibdata))
for bib_id in bibdata.entries:
    
    b = bibdata.entries[bib_id]
    
    paper_title = bibdata.entries[bib_id].fields['title']
    first_author = bib_id[:-3]
    author_list = bibdata.entries[bib_id].persons['author']
    if "bibdata.entries[bib_id].fields['journal']" in locals():
        journal_name = bibdata.entries[bib_id].fields['journal']
    if "bibdata.entries[bib_id].fields['year']" in locals():
        year_pub = bibdata.entries[bib_id].fields['year']
    if "bibdata.entries[bib_id].fields['month']" in locals():
        month_pub = bibdata.entries[bib_id].fields['month']
    #if len(author_list) > 2:
    #    author_list = author_list[:2]
    #print(paper_title)
    #print(author_list[0])
    #print(bib_id)
    #for author in bibdata.entries[bib_id].persons:#.persons["author"]:
        #print(author)
